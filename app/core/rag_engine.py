# app/core/rag_engine.py
"""
Fully Async RAG Engine - CORRECT VERSION

Key insight: similarity_search_by_vector is NOT IMPLEMENTED in langchain_elasticsearch!
So we run the full similarity_search(query) in a thread pool instead.
"""

import asyncio
import os
import re
from typing import Dict, Any, List, Optional
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

# =============================================================================
# THREAD POOL CONFIGURATION
# =============================================================================

# How many concurrent embedding+search operations to allow
# Each worker can handle one similarity_search (which includes embedding)
# Thread pool for ES search (includes embedding inside)
_search_executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
    max_workers=settings.ELASTIC_SEARCH_WORKERS,
    thread_name_prefix="es_search"
)
logger.info(f"ES search executor initialized: {settings.ELASTIC_SEARCH_WORKERS} workers")

# Separate pool for BM25/Reranker if enabled
_cpu_executor: Optional[ThreadPoolExecutor] = None
if settings.USE_BM25 or settings.USE_RERANKER:
    _cpu_executor = ThreadPoolExecutor(
        max_workers=settings.CPU_BOUNDED_WORKERS,
        thread_name_prefix="cpu_ops"
    )
    logger.info(f"CPU executor initialized: {settings.CPU_BOUNDED_WORKERS} workers")


def sanitize_user_input(text: str) -> str:
    """Sanitize user input to prevent prompt injection."""
    if not text:
        return ""
    
    sanitized = text
    sanitized = re.sub(r'<[^>]+>', '', sanitized)
    
    injection_patterns = [
        r'ignore\s+(previous|above|all)\s+instructions?',
        r'disregard\s+(previous|above|all)\s+instructions?',
        r'forget\s+(previous|above|all)\s+instructions?',
        r'you\s+are\s+now\s+',
        r'new\s+instructions?:',
        r'system\s*:',
        r'assistant\s*:',
        r'human\s*:',
        r'\[INST\]',
        r'\[/INST\]',
        r'<<SYS>>',
        r'<</SYS>>',
    ]
    
    for pattern in injection_patterns:
        sanitized = re.sub(pattern, '[FILTERED]', sanitized, flags=re.IGNORECASE)
    return sanitized.strip()


class State(TypedDict):
    """State for LangGraph pipeline"""
    question: str
    conversation_history: str
    enhanced_query: str
    docs: List[LangChainDocument]
    answer: str


class Retriever:
    """
    Hybrid retriever with proper async handling.
    
    KEY INSIGHT: similarity_search_by_vector is NOT IMPLEMENTED in langchain_elasticsearch!
    So we run the full similarity_search(query) in a thread pool.
    """
    
    def __init__(
        self,
        es_store: ElasticsearchStore,
        documents: List[LangChainDocument],
        embeddings: HuggingFaceEmbeddings,
        reranker_model: str,
        output_k: int,
        use_reranker: bool,
        use_bm25: bool
    ):
        self.es_store = es_store
        self.documents = documents
        self.embeddings = embeddings
        self.output_k = output_k
        self.use_reranker = use_reranker
        self.use_bm25 = use_bm25
        
        # BM25 - CPU only
        if self.use_bm25:
            self.bm25_retriever = BM25Retriever.from_documents(documents)
            self.bm25_retriever.k = output_k * 2
            logger.info("✓ BM25 enabled")
        else:
            self.bm25_retriever = None
            logger.info("✗ BM25 disabled")
        
        # Reranker - GPU accelerated
        if self.use_reranker:
            self.reranker = CrossEncoder(
                reranker_model,
                device=settings.DEVICE
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
        Hybrid retrieval with proper parallelism.
        
        ES search runs in thread pool (includes embedding internally).
        BM25 runs in separate thread pool if enabled.
        """
        # Run ES and BM25 in parallel (both in thread pools)
        es_task = self._es_search_in_executor(query, self.output_k * 2)
        
        if self.use_bm25:
            bm25_task = self._bm25_search(query)
            results = await asyncio.gather(es_task, bm25_task, return_exceptions=True)
            es_results = results[0] if not isinstance(results[0], Exception) else []
            bm25_results = results[1] if not isinstance(results[1], Exception) else []
            
            if isinstance(results[0], Exception):
                logger.error("ES search exception", error=str(results[0]))
            if isinstance(results[1], Exception):
                logger.error("BM25 search exception", error=str(results[1]))
        else:
            es_results = await es_task
            if isinstance(es_results, Exception):
                logger.error("ES search exception", error=str(es_results))
                es_results = []
            bm25_results = []
        
        # Combine and deduplicate
        candidates = list(es_results) + list(bm25_results)
        seen = set()
        unique_candidates = []
        
        for doc in candidates:
            doc_id = doc.metadata.get("chunk_id") if doc.metadata else None
            if doc_id and doc_id not in seen:
                seen.add(doc_id)
                unique_candidates.append(doc)
            elif not doc_id:
                content_hash = hash(doc.page_content[:100])
                if content_hash not in seen:
                    seen.add(content_hash)
                    unique_candidates.append(doc)
        
        if not unique_candidates:
            logger.warning("No documents retrieved", query=query[:50])
            return []
        
        # Optionally rerank
        if self.use_reranker and self.reranker:
            return await self._rerank_async(query, unique_candidates)
        
        return unique_candidates[:self.output_k]
    
    async def _es_search_in_executor(self, query: str, k: int) -> List[LangChainDocument]:
        """
        Run ES similarity_search in thread pool.
        
        This is the CORRECT approach because:
        1. similarity_search(query) WORKS (includes embedding internally)
        2. similarity_search_by_vector(embedding) is NOT IMPLEMENTED
        3. Running in thread pool prevents blocking the event loop
        """
        loop = asyncio.get_running_loop()
        
        def do_search():
            """Sync search function - runs in thread pool."""
            try:
                # Use the SYNC method that actually works!
                results = self.es_store.similarity_search(query, k=k)
                return results
            except Exception as e:
                logger.error("ES search error in thread", 
                           error=str(e), 
                           error_type=type(e).__name__)
                return []
        
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_search_executor, do_search),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS
            )
            return results if results else []
        except asyncio.TimeoutError:
            logger.warning("ES search timed out")
            return []
        except Exception as e:
            logger.error("ES search failed", error=str(e))
            return []
    
    async def _bm25_search(self, query: str) -> List[LangChainDocument]:
        """Run BM25 in executor (CPU-bound)."""
        if not _cpu_executor or not self.bm25_retriever:
            return []
        
        loop = asyncio.get_running_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(_cpu_executor, self.bm25_retriever.invoke, query),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS
            )
            return results
        except asyncio.TimeoutError:
            logger.warning("BM25 search timed out")
            return []
        except Exception as e:
            logger.error("BM25 search failed", error=str(e))
            return []
    
    async def _rerank_async(
        self,
        query: str,
        candidates: List[LangChainDocument]
    ) -> List[LangChainDocument]:
        """Rerank documents in executor."""
        if not candidates or not self.use_reranker:
            return candidates[:self.output_k]
        
        loop = asyncio.get_running_loop()
        executor = _cpu_executor or _search_executor
        
        def rerank():
            pairs = [[query, doc.page_content] for doc in candidates]
            scores = self.reranker.predict(pairs)
            doc_scores = list(zip(candidates, scores))
            doc_scores.sort(key=lambda x: x[1], reverse=True)
            return [doc for doc, _ in doc_scores[:self.output_k]]
        
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(executor, rerank),
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
    """Async wrapper for Google GenAI."""
    
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
        """Async LLM generation with timeout."""
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
    Fully async RAG engine.
    
    ES search (with embedding) runs in thread pool for parallelism.
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
        """Initialize RAG components."""
        logger.info("Initializing RAG Engine...")
        
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
        logger.info("✓ Embedding Model Initialized", device=settings.DEVICE)
        
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
        logger.info("✓ LLMClient Initialized" if settings.LLM_TURNED_ON else "✗ LLM Client Disabled")
        
        # Build graph
        self._build_graph()
        logger.info("✓ RAG Engine initialized successfully")
    
    def _build_graph(self):
        """Build LangGraph workflow with async nodes."""
        engine = self
        
        async def enhance_query(state: State) -> Dict:
            """Query enhancement node."""
            if DEBUG_MOD:
                logger.debug("Graph: Enhancing query")
            
            question = sanitize_user_input(state["question"])
            
            instruction = QUERY_ENHANCEMENT_PROMPT.invoke({
                "maxtoken": settings.ENHANCER_OUTPUT_TOKEN
            })
            
            if not settings.LLM_TURNED_ON:
                return {"enhanced_query": question}
            
            try:
                enhanced = await engine.llm.generate(
                    model=settings.QUERY_ENHANCER_MODEL_NAME,
                    system_instruction=instruction,
                    content=f"<user_query>{question}</user_query>",
                    temperature=0.2,
                    top_p=0.7
                )
                return {"enhanced_query": enhanced}
            except Exception as e:
                logger.error("Query enhancement failed, using original", error=str(e))
                return {"enhanced_query": question}
        
        async def retrieve_hybrid(state: State) -> Dict:
            """Hybrid retrieval node."""
            query = state.get("enhanced_query", state["question"])
            docs = await engine.retriever.retrieve(query)
            if DEBUG_MOD:
                logger.debug(f"Retrieved {len(docs)} documents")
            return {"docs": docs}
        
        async def generate_answer(state: State) -> Dict:
            """Answer generation node."""
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
                return {"answer": test_message_collection.test_message_2}
            
            try:
                answer = await engine.llm.generate(
                    model=settings.ANSWER_GENERATOR_MODEL_NAME,
                    system_instruction=instruction,
                    content=f"<user_query>{question}</user_query>",
                    temperature=0.1,
                    top_p=0.9
                )
                return {"answer": answer}
            except Exception as e:
                logger.error("Answer generation failed", error=str(e))
                return {"answer": "I'm sorry, I encountered an error generating a response. Please try again."}
        
        # Build graph
        graph_builder = StateGraph(State)
        graph_builder.add_node("enhance_query", enhance_query)
        graph_builder.add_node("retrieve", retrieve_hybrid)
        graph_builder.add_node("generate", generate_answer)
        
        graph_builder.add_edge(START, "enhance_query")
        graph_builder.add_edge("enhance_query", "retrieve")
        graph_builder.add_edge("retrieve", "generate")
        graph_builder.add_edge("generate", END)
        
        self.graph = graph_builder.compile()
        logger.info("✓ Graph compiled")
    
    async def query(
        self,
        question: str,
        conversation_history: str = ""
    ) -> Dict[str, Any]:
        """Execute fully async RAG query."""
        input_state = {
            "question": question,
            "conversation_history": conversation_history
        }
        
        try:
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
            raise InternalException("Query timeout")
        except Exception as e:
            logger.error("Query error", error=str(e), question=question[:50])
            raise InternalException("Query error")
    
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
            "thread_pool": {
                "ELASTIC_SEARCH_WORKERS": settings.ELASTIC_SEARCH_WORKERS,
                "cpu_workers": _cpu_executor._max_workers if _cpu_executor else 0,
            },
            "timeouts": {
                "llm": settings.LLM_TIMEOUT_SECONDS,
                "retrieval": settings.RETRIEVAL_TIMEOUT_SECONDS,
                "total": settings.TOTAL_QUERY_TIMEOUT_SECONDS
            }
        }


def create_rag_engine() -> RAGEngine:
    """Factory function to create RAG engine."""
    return RAGEngine()


async def cleanup_rag_engine():
    """Cleanup thread pools on shutdown."""
    if _search_executor:
        _search_executor.shutdown(wait=False)
    if _cpu_executor:
        _cpu_executor.shutdown(wait=False)
    logger.info("RAG engine executors cleaned up")
