# app/core/rag_engine.py
"""
Fully Async RAG Engine - CORRECT VERSION

Key insight: similarity_search_by_vector is NOT IMPLEMENTED in langchain_elasticsearch!
So we run the full similarity_search(query) in a thread pool instead.
"""
import asyncio
import os
import re
import json
import structlog
import json
from pathlib import Path
from typing import Dict, Any, List , Literal
from langchain_core.documents import Document as LangChainDocument
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_elasticsearch import ElasticsearchStore
from langgraph.graph import START, StateGraph, END
from app.core.llm_client import LLMClient
from app.core.retriever import Retriever
from app.core.security import sanitize_user_input
from app.exceptions import InternalException
from app.prompts import QUERY_ENHANCEMENT_PROMPT, ANSWER_GENERATION_PROMPT
from app.config import settings
from app.schemas.chat import State
import app.test_message_collection as test_message_collection

logger = structlog.get_logger()

DEBUG_MOD = settings.DEBUG
# Environment setup
os.environ["TOKENIZERS_PARALLELISM"] = "false"
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
    
    def _parse_json_response(self, response_text: str) -> dict:
        """Helper to extract and parse JSON from LLM response."""
        try:
            # Remove markdown code blocks if present
            cleaned = re.sub(r'```json\s*', '', response_text)
            cleaned = re.sub(r'```', '', cleaned)
            return json.loads(cleaned.strip())
        except Exception:
            # Fallback if parsing fails - assume accepted but use raw query
            return {
                "status": "ACCEPTED",
                "resolved_query": "",
                "enhanced_query": response_text,
                "keywords": []
            }
        
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
                logger.info("Index delete skipped", error=str(e))
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
            use_bm25=settings.USE_BM25,
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
                logger.info("Graph: Enhancing query")
            
            question = sanitize_user_input(state["question"])

            if settings.ENABLE_CONVERSATION_MEMORY:
                conversation_history = state.get("conversation_history", "No previous context.")
                short_history_list = conversation_history[-settings.ENHANCER_MEMORY:] if conversation_history else []
                conversation_history = "\n".join(short_history_list) if short_history_list else "No previous context."
            else:
                conversation_history = "No history given - consider this your first message with the user"

            instruction = QUERY_ENHANCEMENT_PROMPT.invoke({
                "maxtoken": str(settings.ENHANCER_OUTPUT_TOKEN),
            }).to_string()
            
            if not settings.LLM_TURNED_ON:
                return {
                    "enhancement_status": "ACCEPTED",
                    "enhanced_query": question,
                    "resolved_query": "",
                    "keywords": []
                }
            
            try:
                raw_response  = await engine.llm.generate(
                    model=settings.QUERY_ENHANCER_MODEL_NAME,
                    system_instruction=instruction,
                    content=f"<user_input>{question}</user_input> \n\n <Conversation_History>{conversation_history}</Conversation_History>",
                    temperature=0.1,
                    top_p=0.5,
                    role="enhancer",
                    thinking_budget=settings.ENHANCER_THINKING_BUDGET,
                )
                data = engine._parse_json_response(raw_response)

                if data.get("status") == "REJECTED":
                    return {
                        "enhancement_status": "REJECTED",
                        "rejection_reason": data.get("reason", "Query rejected by domain filter.")
                    }
                else:
                    return {
                        "enhancement_status": "ACCEPTED",
                        "enhanced_query": data.get("enhanced_query", question),
                        "resolved_query": data.get("resolved_query", ""),
                        "keywords": data.get("keywords", [])
                    }
            except Exception as e:
                logger.error(
                    "Query enhancement CRASHED",
                    error=str(e) if str(e) else "empty error string",
                    error_type=type(e).__name__,
                    error_repr=repr(e),
                )
                return {
                    "enhancement_status": "REJECTED",
                    "rejection_reason": f"server had problem , Enhancement failed",
                }    
            
        def route_query(state: State) -> Literal["retrieve", "end"]:
            """Conditional Edge."""
            # Default to retrieve if status is missing/null
            status = state.get("enhancement_status", "ACCEPTED")
            if status == "REJECTED":
                return "end"
            return "retrieve"
        
        async def retrieve_hybrid(state: State) -> Dict:
            """Hybrid retrieval node."""
            query = state.get("enhanced_query") or state.get("resolved_query") or state["question"]
            keywords = state.get("keywords", [])
            if isinstance(keywords, list):          # ← changed: removed `if keywords and`
                keywords = " ".join(keywords)
            
            search_query = f"{query} {keywords}".strip() if keywords else query
            docs = await engine.retriever.retrieve(search_query)
            if DEBUG_MOD:
                logger.info(f"Retrieved {len(docs)} documents")
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
                conversation_history = state.get("conversation_history", "")[-settings.GENERATOR_MEMORY:]
                conversation_history = "\n".join(conversation_history) if conversation_history else "No history given - consider this your first message with the user"
            else:
                conversation_history = "No history given - consider this your first message with the user"
            
            resolved_query = state.get("resolved_query", "")

            instruction = ANSWER_GENERATION_PROMPT.invoke({
                "maxtoken": settings.ANSWER_LLM_OUTPUT_TOKEN
            }).to_string()
            
            if not settings.LLM_TURNED_ON:
                return {"answer": test_message_collection.test_message_2}
            
            # In generate_answer node:
            try:
                answer = await engine.llm.generate(
                    model=settings.ANSWER_GENERATOR_MODEL_NAME,
                    system_instruction=instruction,
                    content=f"<user_input>{question}</user_input>\n\n<resolved_query>{resolved_query}</resolved_query>\n\n<Conversation_History>{conversation_history}</Conversation_History>\n\n <Retrieved_Documents>{docs_content}</Retrieved_Documents>",
                    temperature=0.1,
                    top_p=0.9,
                    role="generator",
                    thinking_budget=settings.GENERATOR_THINKING_BUDGET,
                )
                return {"answer": answer}
            except Exception as e:
                    logger.error("Answer generation failed", error=str(e))
                    return {"answer": "I'm sorry, I encountered an error."}
                    
        # Build graph
        graph_builder = StateGraph(State)
        graph_builder.add_node("enhance_query", enhance_query)
        graph_builder.add_node("retrieve", retrieve_hybrid)
        graph_builder.add_node("generate", generate_answer)
        
        graph_builder.add_edge(START, "enhance_query")
        graph_builder.add_conditional_edges(
            "enhance_query",
            route_query,
            {
                "retrieve": "retrieve",
                "end": END
            }
        )
        graph_builder.add_edge("retrieve", "generate")
        graph_builder.add_edge("generate", END)
        
        self.graph = graph_builder.compile()
        logger.info("✓ Graph compiled")
    
    async def query(
        self,
        question: str,
        conversation_history: List[str] 
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
            
            if result.get("enhancement_status") == "REJECTED":
                final_answer = result.get("rejection_reason", "answer rejected.")
                retrieved_docs = []
            else:
                final_answer = result.get("answer")
                retrieved_docs = [
                    {"content": doc.page_content, "metadata": doc.metadata}
                    for doc in result.get("docs", [])
                ]

            return {
                "question": result.get("question"),
                "resolved_query": result.get("resolved_query"),
                "enhanced_query": result.get("enhanced_query"),
                "keywords" : result.get("keywords", []),
                "answer": final_answer,
                "usage": {},
                "retrieved_docs": retrieved_docs,
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
                "cpu_workers": settings.CPU_BOUNDED_WORKERS if settings.CPU_BOUNDED_WORKERS else 0,
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
