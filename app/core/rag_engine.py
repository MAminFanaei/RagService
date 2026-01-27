
import asyncio
import os
from typing import Dict, Any, List
from concurrent.futures import ThreadPoolExecutor
import structlog

from langchain_core.documents import Document as LangChainDocument
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
from langchain_elasticsearch import ElasticsearchStore
from langgraph.graph import START, StateGraph, END
from langchain_community.retrievers import BM25Retriever
from typing import TypedDict
import json
from pathlib import Path

from google import genai
from google.genai import types

from app.exceptions import InternalException
from app.prompts import QUERY_ENHANCEMENT_PROMPT, ANSWER_GENERATION_PROMPT
from app.config import settings
import app.test_message_collection as test_message_collection

logger = structlog.get_logger()

# Environment setup
os.environ["TOKENIZERS_PARALLELISM"] = "false"
DEBUG_MOD = settings.DEBUG

# Thread pool for CPU-bound operations
_executor = ThreadPoolExecutor(max_workers=settings.WORKERS)


class State(TypedDict):
    """State for LangGraph pipeline"""
    question: str
    conversation_history: str
    enhanced_query: str
    docs: List[LangChainDocument]
    answer: str


class Retriever:
    """
     hybrid retriever with configurable components.
    
    - ES: Always enabled (primary)
    - BM25: Optional (config toggle)
    - Reranker: Optional, GPU-accelerated (config toggle)
    """
    
    def __init__(
        self,
        es_store: ElasticsearchStore,
        documents: List[LangChainDocument],
        embeddings: HuggingFaceEmbeddings,
        reranker_model: str,
        output_k: int,
        use_reranker: bool,
        use_bm25: bool  # NEW parameter
    ):
        self.es_store = es_store
        self.documents = documents
        self.embeddings = embeddings
        self.output_k = output_k
        self.use_reranker = use_reranker
        self.use_bm25 = use_bm25
        
        # BM25 - CPU only (sparse retrieval, no GPU version exists)
        if self.use_bm25:
            self.bm25_retriever = BM25Retriever.from_documents(documents)
            self.bm25_retriever.k = output_k * 2
            logger.info("✓ BM25 enabled")
        else:
            self.bm25_retriever = None
            logger.info("✗ BM25 disabled")
        
        # Reranker - GPU accelerated via DEVICE setting
        if self.use_reranker:
            self.reranker = CrossEncoder(
                reranker_model,
                device=settings.DEVICE  # "cuda" or "cpu"
            )
            logger.info("✓ Reranker enabled", device=settings.DEVICE)
        else:
            self.reranker = None
            logger.info("✗ Reranker disabled")
        
        logger.info("Retriever initialized", 
                    use_bm25=use_bm25,
                    use_reranker=use_reranker, 
                    output_k=output_k)
    
    async def retrieve(self, query: str) -> List[LangChainDocument]:
        """
        hybrid retrieval with reranking.
        """
        # Always run ES
        es_task = self._es_search(query, self.output_k * 2)
        
        # Optionally run BM25
        if self.use_bm25:
            bm25_task = self._bm25_search(query)
            results = await asyncio.gather(es_task, bm25_task, return_exceptions=True)
            es_results = results[0] if not isinstance(results[0], Exception) else []
            bm25_results = results[1] if not isinstance(results[1], Exception) else []
        else:
            es_results = await es_task
            if isinstance(es_results, Exception):
                es_results = []
            bm25_results = []
        
        # Combine and deduplicate
        candidates = es_results + bm25_results
        seen = set()
        unique_candidates = []
        
        for doc in candidates:
            doc_id = doc.metadata.get("chunk_id")
            if doc_id not in seen:
                seen.add(doc_id)
                unique_candidates.append(doc)
        
        if not unique_candidates:
            logger.warning("No documents retrieved", query=query[:50])
            return []
        
        # Optionally rerank
        if self.use_reranker and self.reranker:
            return await self._reranker(query, unique_candidates)
        
        return unique_candidates[:self.output_k]
    
    async def _es_search(self, query: str, k: int) -> List[LangChainDocument]:
        """Elasticsearch similarity search."""
        try:
            # langchain-elasticsearch supports async
            results = await asyncio.wait_for(
                self.es_store.asimilarity_search(query, k=k),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS
            )
            return results
        except asyncio.TimeoutError:
            logger.warning("ES search timed out", query=query[:50])
            return []
        except Exception as e:
            logger.error("ES search failed", error=str(e))
            return []
    
    async def _bm25_search(self, query: str) -> List[LangChainDocument]:
        """Run BM25 in executor (CPU-bound)."""
        loop = asyncio.get_running_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_executor, self.bm25_retriever.invoke, query),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS
            )
            return results
        except asyncio.TimeoutError:
            logger.warning("BM25 search timed out", query=query[:50])
            return []
        except Exception as e:
            logger.error("BM25 search failed", error=str(e))
            return []
    
    async def _reranker(
        self,
        query: str,
        candidates: List[LangChainDocument]
    ) -> List[LangChainDocument]:
        """Rerank documents in executor (CPU-bound)."""
        if not candidates or not self.use_reranker:
            return candidates[:self.output_k]
        
        loop = asyncio.get_running_loop()
        
        def rerank():
            pairs = [[query, doc.page_content] for doc in candidates]
            scores = self.reranker.predict(pairs)
            doc_scores = list(zip(candidates, scores))
            doc_scores.sort(key=lambda x: x[1], reverse=True)
            return [doc for doc, _ in doc_scores[:self.output_k]]
        
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_executor, rerank),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS
            )
            return results
        except asyncio.TimeoutError:
            logger.warning("Reranking timed out")
            return candidates[:self.output_k]
        except Exception as e:
            logger.error("Reranking failed", error=str(e))
            return candidates[:self.output_k]
    

class LLMClient:
    """
    wrapper for Google GenAI.
    
    Uses generate_content_async for true async LLM calls.
    """
    
    def __init__(self, api_key: str, base_url: str):
        self.client = genai.Client(
            api_key=api_key,
            http_options={"base_url": base_url}
        )
        logger.info("LLMClient initialized")
    
    async def generate(
        self,
        model: str,
        system_instruction: str,
        content: str,
        temperature: float = 0.2,
        top_p: float = 0.7
    ) -> str:
        """
        LLM generation with timeout.
        """
        try:
            response = await asyncio.wait_for(
                self.client.aio.models.generate_content(
                    model=model,
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        temperature=temperature,
                        top_p=top_p
                    ),
                    contents=content,
                ),
                timeout=settings.LLM_TIMEOUT_SECONDS
            )
            return response.text.strip()
        except asyncio.TimeoutError:
            logger.error("LLM generation timed out", model=model)
            raise TimeoutError(f"LLM generation timed out after {settings.LLM_TIMEOUT_SECONDS}s")
        except Exception as e:
            logger.error("LLM generation failed", error=str(e), model=model)
            raise


class RAGEngine:
    """
    Fully async RAG engine with:
    -  LLM calls via google-genai async API
    -  Elasticsearch via langchain async methods
    - CPU-bound operations in thread executor
    - Configurable timeouts
    - LangGraph with async nodes
    """
    
    _instance = None
    _initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if not self._initialized:
            self._init_rag()
            RAGEngine._initialized = True
    
    def _init_rag(self):
        """Initialize RAG components (sync initialization)."""
        logger.info("Initializing  RAG Engine...")
        
        # Initialize embeddings
        self.embeddings = HuggingFaceEmbeddings(
            model_name=settings.EMBEDDING_MODEL_PATH,
            model_kwargs={
                "device": settings.DEVICE,
                "local_files_only": True,
                "trust_remote_code": True,
            },
            encode_kwargs={'normalize_embeddings': True}
        )
        logger.info("✓ Embedding Model Initialized")
        
        # Initialize Elasticsearch store
        self.es_store = ElasticsearchStore(
            es_url=settings.elasticsearch_url,
            index_name=settings.ELASTICSEARCH_INDEX_NAME,
            embedding=self.embeddings,
            es_user=settings.ELASTICSEARCH_USERNAME,
            es_password=settings.ELASTICSEARCH_PASSWORD
        )
        logger.info("✓ Elasticsearch Initialized")
        
        # Load documents
        self.docs = []
        docs_path = Path(settings.DOC_PATH)
        for json_file in docs_path.glob("*.json"):
            with open(json_file, 'r', encoding='utf-8') as f:
                sections = json.load(f)
            for section in sections:
                metadata = {k: v for k, v in section.items() if k != "chunk_text"}
                doc = LangChainDocument(
                    page_content=section.get("chunk_text", ""),
                    metadata=metadata
                )
                self.docs.append(doc)
        logger.info(f"✓ Loaded {len(self.docs)} documents from {docs_path}")
        
        # Index documents if needed
        if settings.INDEX_THE_DOCS:
            try:
                self.es_store.client.indices.delete(index=settings.ELASTICSEARCH_INDEX_NAME)
                logger.info("Old documents erased", index=settings.ELASTICSEARCH_INDEX_NAME)
            except Exception as e:
                logger.debug("Index delete skipped", error=str(e))
            finally:
                self.es_store.add_documents(self.docs)
                logger.info(f"✓ Indexed {len(self.docs)} documents")
        
        # Initialize retriever
        self.retriever = Retriever(
            es_store=self.es_store,
            documents=self.docs,
            embeddings=self.embeddings,
            reranker_model=settings.RERANKER_MODEL_PATH,
            output_k=settings.RETRIEVER_OUTPUT_K,
            use_reranker=settings.USE_RERANKER,
            use_bm25=settings.USE_BM25 
        )

        logger.info("✓ Retriever Initialized")
        
        # Initialize LLM client
        self.llm = LLMClient(
            api_key=settings.LLM_API_KEY,
            base_url=settings.LLM_BASE_URL
        )
        logger.info("✓ LLMClient Initialized" if settings.LLM_TURNED_ON else "⚠ LLM Client Disabled")
        
        # Build graph
        self._build_graph()
        logger.info("✓  RAG Engine initialized successfully")
    

    def _build_graph(self):
        """Build LangGraph workflow with nodes."""
        
        # Store self reference for closures
        engine = self # such a nerdy move

        # ------------------------------- Graph Nodes ---------------------------------
        async def enhance_query(state: State) -> Dict:
            """ query enhancement."""
            logger.info("Graph: Enhancing query") if DEBUG_MOD else None
            
            question = state["question"]
            
            instruction = QUERY_ENHANCEMENT_PROMPT.invoke({
                "maxtoken": settings.ENHANCER_OUTPUT_TOKEN
            })
            
            if not settings.LLM_TURNED_ON:
                logger.warning("Test query enhanced") if DEBUG_MOD else None
                return {"enhanced_query": question}
            
            try:
                enhanced = await engine.llm.generate(
                    model=settings.QUERY_ENHANCER_MODEL_NAME,
                    system_instruction=instruction,
                    content=f"<user_query>{question}</user_query>",
                    temperature=0.2,
                    top_p=0.7
                )
                logger.info("Query enhanced") if DEBUG_MOD else None
                return {"enhanced_query": enhanced}
            except Exception as e:
                logger.error("Query enhancement failed, using original", error=str(e))
                return {"enhanced_query": question}
            
        # ------------------------------- Graph Nodes ---------------------------------
        async def retrieve_hybrid(state: State) -> Dict:
            """ hybrid retrieval."""
            query = state.get("enhanced_query", state["question"])
            
            docs = await engine.retriever.retrieve(query)
            
            logger.info(f"Retrieved {len(docs)} documents") if DEBUG_MOD else None
            return {"docs": docs}
        
        # ------------------------------- Graph Nodes ---------------------------------
        async def generate_answer(state: State) -> Dict:
            """ answer generation."""
            question = state["question"]
            
            if state.get("docs"):
                docs_content = "\n\n---Document Separator---\n\n".join([
                    doc.page_content for doc in state["docs"]
                ])
            else:
                docs_content = "internal Error - NO Document Retrieved!"
            
            if settings.ENABLE_CONVERSATION_MEMORY:
                conversation_history = state.get("conversation_history", "")
            else:
                conversation_history = "No history given"
            
            instruction = ANSWER_GENERATION_PROMPT.invoke({
                "context": docs_content,
                "maxtoken": settings.ANSWER_LLM_OUTPUT_TOKEN,
                "conversation_history": conversation_history
            })
            
            if not settings.LLM_TURNED_ON:
                logger.warning("Test answer generated") if DEBUG_MOD else None
                return {"answer": test_message_collection.test_message_2}
            
            # if LLM IS ON :
            try:
                answer = await engine.llm.generate(
                    model=settings.ANSWER_GENERATOR_MODEL_NAME,
                    system_instruction=instruction,
                    content=f"<user_query>{question}</user_query>",
                    temperature=0.1,
                    top_p=0.9
                )
                logger.info("Answer generated") if DEBUG_MOD else None
                return {"answer": answer}
            
            except Exception as e:
                logger.error("Answer generation failed", error=str(e))
                return {"answer": "I'm sorry, I encountered an error generating a response. Please try again."}
        
        # Build graph with async nodes
        graph_builder = StateGraph(State)
        graph_builder.add_node("enhance_query", enhance_query)
        graph_builder.add_node("retrieve", retrieve_hybrid)
        graph_builder.add_node("generate", generate_answer)
        
        graph_builder.add_edge(START, "enhance_query")
        graph_builder.add_edge("enhance_query", "retrieve")
        graph_builder.add_edge("retrieve", "generate")
        graph_builder.add_edge("generate", END)
        
        self.graph = graph_builder.compile()
        logger.info("✓  graph compiled")

    async def query(
        self,
        question: str,
        conversation_history: str = ""
    ) -> Dict[str, Any]:
        """
        Execute fully async RAG query.
        
        Args:
            question: User's question
            conversation_history: Formatted conversation history
            
        Returns:
            Dict with answer, enhanced_query, retrieved_docs, etc.
        """
        input_state = {
            "question": question,
            "conversation_history": conversation_history
        }
        
        try:
            # graph execution
            result = await asyncio.wait_for(
                self.graph.ainvoke(input_state),
                timeout=settings.TOTAL_QUERY_TIMEOUT_SECONDS
            )
            
            return {
                "question": result.get("question"),
                "enhanced_query": result.get("enhanced_query"),
                "answer": result.get("answer"),
                "usage": {},
                "retrieved_docs": [
                    {"content": doc.page_content, "metadata": doc.metadata}
                    for doc in result.get("docs", [])
                ],
                "had_conversation_context": bool(conversation_history)
            }
        
        except asyncio.TimeoutError:
            logger.error("Total query timeout", question=question[:50])
            raise InternalException("query timeout")

        
        except Exception as e:
            logger.error("an error occurred", error=str(e), question=question[:50])
            raise InternalException("an error occurred")
            
    
    def get_stats(self) -> Dict[str, Any]:
        """Get RAG engine statistics."""
        return {
            "model": settings.EMBEDDING_MODEL_PATH,
            "index": settings.ELASTICSEARCH_INDEX_NAME,
            "documents_count": len(self.docs),
            "device": settings.DEVICE,
            "memory_enabled": settings.ENABLE_CONVERSATION_MEMORY,
            "max_history_messages": settings.MEMORY_MAX_MESSAGES,
            "async": True,
            "timeouts": {
                "llm": settings.LLM_TIMEOUT_SECONDS,
                "retrieval": settings.RETRIEVAL_TIMEOUT_SECONDS,
                "total": settings.TOTAL_QUERY_TIMEOUT_SECONDS
            }
        }



def create_rag_engine() -> RAGEngine:
    """Factory function to create RAG engine."""
    return RAGEngine()