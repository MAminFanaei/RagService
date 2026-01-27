import asyncio
import os
from typing import Dict, Any, List, Optional
import structlog
from langchain_core.documents import Document as LangChainDocument
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
from langchain_elasticsearch import ElasticsearchStore
from langgraph.graph import START, StateGraph, END
from langchain_core.prompts import PromptTemplate
from langchain_community.retrievers import BM25Retriever
from typing import TypedDict
import json
from pathlib import Path
from app.prompts import (
    QUERY_ENHANCEMENT_PROMPT,
    ANSWER_GENERATION_PROMPT
)
from google import genai
from google.genai import types
from app.config import settings
import app.test_message_collection as test_message_collection

logger = structlog.get_logger()
# Set environment for tokenizers
os.environ["TOKENIZERS_PARALLELISM"] = "false"
DEBUG_MOD = settings.DEBUG

class State(TypedDict):
    """State for LangGraph pipeline"""
    # Input
    question: str
    conversation_history: str  # NEW: Formatted conversation history
    
    # Processing
    enhanced_query: str
    docs: List[LangChainDocument]
    
    # Output
    answer: str


class Retriever:
    """Hybrid retriever combining multiple retrieval strategies"""
    
    def __init__(
        self,
        es_store,
        documents,
        embeddings,
        reranker_model,
        output_k,
        use_reranker: bool
    ):
        self.es_store = es_store
        self.documents = documents
        self.embeddings = embeddings
        self.reranker_model = reranker_model
        self.bm25_retriever = BM25Retriever.from_documents(documents)
        self.output_k = output_k
        self.bm25_retriever.k = output_k * 2
        self.use_reranker = use_reranker
        
        if self.use_reranker:
            self.reranker = CrossEncoder(reranker_model)
    
    def retrieve_with_reranking(
        self,
        query: str,
    ) -> List[LangChainDocument]:
        """
        Perform hybrid retrieval with reranking.
        
        Args:
            query: The enhanced query
        """
        # Get candidates from both retrievers
        es_results = self.es_store.similarity_search(query, k=self.output_k )
        
        # Use invoke() instead of deprecated get_relevant_documents()
        bm25_results = self.bm25_retriever.invoke(query)
        
        # Combine and deduplicate
        candidates = es_results + bm25_results
        seen = set()
        unique_candidates = []
        for candid in candidates:
            doc_id = candid.metadata["chunk_id"]
            if doc_id not in seen:
                seen.add(doc_id)
                unique_candidates.append(candid)
        
        if not unique_candidates:
            return []
        
        if self.use_reranker:
            pairs = [[query, doc.page_content] for doc in unique_candidates]
            scores = self.reranker.predict(pairs)
            doc_scores = list(zip(unique_candidates, scores))
            doc_scores.sort(key=lambda x: x[1], reverse=True)
            return [doc for doc, _ in doc_scores[:self.output_k]]
        else:
            return unique_candidates[:self.output_k]


class RAGEngine:
    """
    Singleton RAG engine with conversation memory support.
    
    The engine now accepts conversation history and uses it to:
    1. Enhance queries with context (resolve pronouns, references)
    2. Generate answers that maintain conversation coherence
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
        """Initialize RAG components"""
        logger.info("Initializing RAG Engine with Memory Support...")
        
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
        
        # Load documents for keyword search -- DO NOT DELETE THIS!!!!
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
        self.hybrid_retriever = Retriever(
            es_store=self.es_store,
            documents=self.docs,
            embeddings=self.embeddings,
            reranker_model=settings.RERANKER_MODEL_PATH,
            output_k=settings.RETRIEVER_OUTPUT_K,
            use_reranker=settings.USE_RERANKER
        )
        logger.info("✓ Retriever Initialized")

        # Initialize LLM client
        self.llm_client = genai.Client(
            api_key=settings.LLM_API_KEY,
            http_options={"base_url": settings.LLM_BASE_URL}
        )
        logger.info("✓ LLM Client Initialized") if settings.LLM_TURNED_ON else logger.warning("LLM Client Is Disabled")

        # Build graph
        self._build_graph()
        logger.info("✓ RAG Engine with Memory initialized successfully")
    
    def _build_graph(self):
        """Build the LangGraph workflow with memory support"""
        
        def enhance_query(state: State) -> Dict:
            """
            Enhance query with conversation context.
            
            If conversation history is provided, uses it to:
            - Resolve pronouns (it, that, this, etc.)
            - Understand follow-up questions
            - Maintain topic continuity
            """
            logger.info("Graph invoked - Enhancing query") if DEBUG_MOD else None
            
            question = state["question"]
                # Use standard enhancement
            instruction = QUERY_ENHANCEMENT_PROMPT.invoke({
                "maxtoken": settings.ENHANCER_OUTPUT_TOKEN
                })
            
            if settings.LLM_TURNED_ON:
                response = self.llm_client.models.generate_content(
                    model=settings.QUERY_ENHANCER_MODEL_NAME,
                    config=types.GenerateContentConfig(
                        system_instruction=instruction,
                        temperature=0.2,
                        top_p=0.7
                    ),
                    contents=f"<user_query>{question}</user_query>",
                )
                enhanced = response.text.strip()
                logger.info(f"Query enhanced: ...") if DEBUG_MOD else None
                return {"enhanced_query": enhanced}
            
            logger.warning("Test query enhanced") if DEBUG_MOD else None
            return {"enhanced_query": question}
        
        def retrieve_hybrid(state: State) -> Dict:
            """Retrieve documents with optional conversation context."""
            query = state.get("enhanced_query", state["question"])
            
            retrieved_docs = self.hybrid_retriever.retrieve_with_reranking(
                query=query,
            )
            
            logger.info(f"Retrieved {len(retrieved_docs)} documents") if DEBUG_MOD else None
            return {"docs": retrieved_docs}
        
        def generate_answer(state: State) -> Dict:
            """
            Generate answer considering conversation history.
            
            If history is provided, the LLM will:
            - Maintain consistency with previous answers
            - Avoid repeating information
            - Build on previous context naturally
            """

            question = state["question"]
            
            if state.get("docs"):
                docs_content = "\n\n---Document Seperator---\n\n".join([
                doc.page_content for doc in state["docs"]])
            else :
                docs_content = "Error - NO Document Retrieved , probebly due to server errors!"
            
            if settings.ENABLE_CONVERSATION_MEMORY:
                conversation_history = state.get("conversation_history", "")
            else:
                conversation_history = "No history given or found , Consider this the first time talking to the user"

            instruction = ANSWER_GENERATION_PROMPT.invoke({
                "context" : docs_content,
                "maxtoken": settings.ANSWER_LLM_OUTPUT_TOKEN,
                "conversation_history": conversation_history
                })
            
            if settings.LLM_TURNED_ON:
                response = self.llm_client.models.generate_content(
                    model=settings.ANSWER_GENERATOR_MODEL_NAME,
                    config=types.GenerateContentConfig(
                        system_instruction=instruction,
                        temperature=0.1,
                        top_p=0.9
                    ),
                    contents=f"<user_query>{question}</user_query>",
                )
                logger.info("Answer generated") if DEBUG_MOD else None
                return {"answer": response.text}
            
            logger.warning("Test answer generated") if DEBUG_MOD else None
            return {"answer": test_message_collection.test_message_2}
        
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
        """
        Execute RAG query with optional conversation history.
        
        Args:
            question: The user's current question
            conversation_history: Formatted string of previous conversation
            
        Returns:
            Dict with question, enhanced_query, answer, usage, retrieved_docs
        """
        loop = asyncio.get_event_loop()
        
        # Prepare input state
        input_state = {
            "question": question,
            "conversation_history": conversation_history
        }
        
        result = await loop.run_in_executor(
            None,
            self.graph.invoke,
            input_state
        )
        
        return {
            "question": result.get("question"),
            "enhanced_query": result.get("enhanced_query"),
            "answer": result.get("answer"),
            "usage": {},  # Add usage tracking if needed
            "retrieved_docs": [
                {"content": doc.page_content, "metadata": doc.metadata}
                for doc in result.get("docs", [])
            ],
            "had_conversation_context": bool(conversation_history)
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get RAG engine statistics"""
        return {
            "model": settings.EMBEDDING_MODEL_PATH,
            "index": settings.ELASTICSEARCH_INDEX_NAME,
            "documents_count": len(self.docs),
            "device": settings.DEVICE,
            "memory_enabled": settings.ENABLE_CONVERSATION_MEMORY,
            "max_history_messages": settings.MEMORY_MAX_MESSAGES
        }

# creates an instance 
def create_rag_engine():
    return RAGEngine()

