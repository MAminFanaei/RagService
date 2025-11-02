import asyncio
import os
from typing import Dict, Any, List
import torch
from langchain_core.documents import Document as LangChainDocument
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
from langchain_elasticsearch import ElasticsearchStore
from langchain_google_genai import ChatGoogleGenerativeAI
from langgraph.graph import START, StateGraph, END
from langchain_core.messages.human import HumanMessage
from langchain_core.messages.system import SystemMessage
from langchain_core.prompts import PromptTemplate
from langchain_community.retrievers import BM25Retriever
from typing import TypedDict
import json
from pathlib import Path
from app.prompts import QUERY_ENHANCEMENT_PROMPT, ANSWER_GENERATION_PROMPT

from app.config import settings

# Set environment for tokenizers
os.environ["TOKENIZERS_PARALLELISM"] = "false"
DEBUG_MOD = settings.DEBUG

class State(TypedDict):
    question: str
    enhanced_query: str
    docs: List[LangChainDocument]
    answer: str
    full_response: object


class Retriever:
    """Hybrid retriever combining multiple retrieval strategies"""
    
    def __init__(self, es_store, documents, embeddings, reranker_model, output_k, use_reranker: bool):
        self.es_store = es_store
        self.documents = documents
        self.embeddings = embeddings
        self.reranker_model = reranker_model
        self.bm25_retriever = BM25Retriever.from_documents(documents)
        self.bm25_retriever.k = 15
        self.output_k = output_k
        self.use_reranker = use_reranker
        
        if self.use_reranker:
            self.reranker = CrossEncoder(reranker_model)
    
    def retrieve_with_reranking(self, query: str) -> List[LangChainDocument]:
        """Perform hybrid retrieval with reranking"""
        # Get candidates from both retrievers
        es_results = self.es_store.similarity_search(query, k=15)
        bm25_results = self.bm25_retriever.get_relevant_documents(query) #🔴 depricated
        
        # Combine and deduplicate
        candidates = es_results + bm25_results
        seen = set()
        unique_candidates = []
        for doc in candidates:
            doc_id = doc.page_content[:100]
            if doc_id not in seen:
                seen.add(doc_id)
                unique_candidates.append(doc)
        
        if not unique_candidates:
            return []
        
        if self.use_reranker:
            pairs = [[query, doc.page_content] for doc in unique_candidates]
            scores = self.reranker.predict(pairs)
            doc_scores = list(zip(unique_candidates, scores))
            doc_scores.sort(key=lambda x: x[1], reverse=True)
            return [doc for doc, score in doc_scores[:self.output_k]]
        else:
            return unique_candidates[:self.output_k]


class RAGEngine:
    """Singleton RAG engine wrapping the user's pipeline"""
    
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
        print("Initializing RAG Engine...")
        
        # Initialize embeddings
        model_path = settings.EMBEDDING_MODEL_NAME
        self.embeddings = HuggingFaceEmbeddings(
            model_name=model_path,
            model_kwargs={
                "device": "cuda" if torch.cuda.is_available() else "cpu",
                "local_files_only": True,
                "trust_remote_code": True,
            },
            encode_kwargs={'normalize_embeddings': True}
        )
        
        print("Embedding Model Initialized") if DEBUG_MOD else None

        # Initialize Elasticsearch store
        self.es_store = ElasticsearchStore(
            es_url=settings.elasticsearch_url,
            index_name=settings.ELASTICSEARCH_INDEX_NAME,
            embedding=self.embeddings,
            es_user=settings.ELASTICSEARCH_USERNAME,
            es_password=settings.ELASTICSEARCH_PASSWORD
        )

        print("Elastic Initialized") if DEBUG_MOD else None
        
        self.docs = []
        docs_path = Path(settings.DOC_PATH) 
        for json_file in docs_path.glob("*.json"):
            with open(json_file, 'r', encoding='utf-8') as f:
                sections = json.load(f)
                
            # Convert to LangChain documents
            for section in sections:
                metadata = {k: v for k, v in section.items() if k != "chunk_text"}
                doc = LangChainDocument(
                    page_content=section.get("chunk_text", ""),
                    metadata=metadata
                )
                self.docs.append(doc)
        
        print(f"Loaded {len(self.docs)} documents from {docs_path}")

        if settings.INDEX_THE_DOCS :
            try:
                self.es_store.client.indices.delete(index=settings.ELASTICSEARCH_INDEX_NAME)
                print(f"old documents got eraised")
            except Exception as e:
                pass
            finally:
                self.es_store.add_documents(self.docs)
            print(f"{len(self.docs)} documents Indexed from {docs_path}")

        # Initialize retriever
        self.hybrid_retriever = Retriever(
            es_store=self.es_store,
            documents=self.docs,
            embeddings=self.embeddings,
            reranker_model=settings.RERANKER_MODEL_NAME,
            output_k=10,
            use_reranker=settings.USE_RERANKER  # Set based on your config
        )
        print("Retriever Initialized") if DEBUG_MOD else None

        # Initialize LLMs
        self.query_enhancer_llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-pro",
            temperature=0.2,
            thinking_budget=int(settings.GENERATOR_THINKING_BUDGET/2),
            max_tokens=settings.ENHANCER_MAX_TOKEN,
            max_retries=1,
            google_api_key=settings.GEMINI_API_KEY
        )
        print("Query Enhancer Initialized") if DEBUG_MOD else None
        
        self.answer_generator_llm = ChatGoogleGenerativeAI(
            model="gemini-2.5-pro",
            temperature=0.1,
            max_tokens=settings.ANSWER_LLM_MAX_TOKEN,
            thinking_budget=settings.GENERATOR_THINKING_BUDGET,
            max_retries=1,
            google_api_key=settings.GEMINI_API_KEY
        )
        print("Answer Generator Initialized") if DEBUG_MOD else None

        # Build graph
        self._build_graph()
        print("RAG Engine initialized successfully")
    
    def _build_graph(self):
        """Build the LangGraph workflow"""
        
        def enhance_query(state: State):
            print("🟢graph invoked") if DEBUG_MOD else None
            messages = QUERY_ENHANCEMENT_PROMPT.invoke({
                "question": state["question"],
                "maxtoken": int(settings.ENHANCER_MAX_TOKEN * 0.4)
            })
            if settings.LLM_TURNED_ON :
                response = self.query_enhancer_llm.invoke(messages)
                enhanced = response.content.strip()
                print(f"🟢Query got Enhanced : {enhanced}") if DEBUG_MOD else None
                return {"enhanced_query": enhanced}
            
            print(f"🟡Test query got Enhanced ") if DEBUG_MOD else None
            return {"enhanced_query": " test - جهاد تبیین"}
        
        def retrieve_hybrid(state: State):
            query = state.get("enhanced_query", state["question"])
            retrieved_docs = self.hybrid_retriever.retrieve_with_reranking(query)

            print("🟢Docs Retrieved") if DEBUG_MOD else None

            return {"docs": retrieved_docs}
        
        def generate_answer(state: State):
            if not state.get("docs"):
                return {"answer": "I don't know - no relevant documents were retrieved"}
            
            docs_content = "\n\n---Document Separator---\n\n".join([
                f"Document {i+1}:\n{doc.page_content}"
                for i, doc in enumerate(state["docs"])
            ])
            
            messages = ANSWER_GENERATION_PROMPT.invoke({
                "question": state["question"],
                "context": docs_content,
                "maxtoken": int(settings.ANSWER_LLM_MAX_TOKEN * 0.7)
            })

            if settings.LLM_TURNED_ON :
                response = self.answer_generator_llm.invoke(messages)
                print("🟢Answer Generated") if DEBUG_MOD else None
                return {"answer": response.content, "full_response": response} 
            
            print("🟡Test answer Generated") if DEBUG_MOD else None
            return {"answer": "test",}
        
        graph_builder = StateGraph(State)
        graph_builder.add_node("enhance_query", enhance_query)
        graph_builder.add_node("retrieve", retrieve_hybrid)
        graph_builder.add_node("generate", generate_answer)
        
        graph_builder.add_edge(START, "enhance_query")
        graph_builder.add_edge("enhance_query", "retrieve")
        graph_builder.add_edge("retrieve", "generate")
        graph_builder.add_edge("generate", END)
        
        self.graph = graph_builder.compile()
    
    async def query(self, question: str) -> Dict[str, Any]:
        """Execute RAG query asynchronously"""
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            self.graph.invoke,
            {"question": question}
        )
        
        return {
            "question": result.get("question"),
            "enhanced_query": result.get("enhanced_query"),
            "answer": result.get("answer"),
            "retrieved_docs": [{"content": doc.page_content,"metadata": doc.metadata} for doc in result.get("docs", [])],
            # "full_responce" : result.get("full_response") #🔴🔴🔴 Remove it for production
        }
    
    def get_stats(self) -> Dict[str, Any]:
        """Get RAG engine statistics"""
        return {
            "model": settings.EMBEDDING_MODEL_NAME,
            "index": settings.ELASTICSEARCH_INDEX_NAME,
            "documents_count": len(self.docs),
            "device": "cuda" if torch.cuda.is_available() else "cpu"
        }

# Singleton instance
rag_engine = RAGEngine()