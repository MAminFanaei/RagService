
import asyncio
import os
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from langchain_core.documents import Document as LangChainDocument
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
from langchain_elasticsearch import ElasticsearchStore
from langchain_community.retrievers import BM25Retriever
import structlog
from app.config import settings

logger = structlog.get_logger()

# Environment setup
os.environ["TOKENIZERS_PARALLELISM"] = "false"

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
        self._search_executor = ThreadPoolExecutor(
            max_workers=settings.ELASTIC_SEARCH_WORKERS,
            thread_name_prefix="es_search")
        
        self._cpu_executor = None
        if self.use_bm25 or self.use_reranker:
            self._cpu_executor = ThreadPoolExecutor(
                max_workers=settings.CPU_BOUNDED_WORKERS,
                thread_name_prefix="cpu_ops"
            )
            logger.info(f"CPU executor initialized: {settings.CPU_BOUNDED_WORKERS} workers")

        # BM25 - CPU only
        if self.use_bm25:
            self.bm25_retriever = BM25Retriever.from_documents(documents)
            self.bm25_retriever.k = output_k * 2
            logger.info("✓ BM25 enabled")
        else:
            self.bm25_retriever = None
        
        # Reranker - GPU accelerated
        if self.use_reranker:
            self.reranker = CrossEncoder(
                reranker_model,
                device=settings.DEVICE
            )
            logger.info("✓ Reranker enabled", device=settings.DEVICE)
        else:
            self.reranker = None
    
    
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
                loop.run_in_executor(self._search_executor, do_search),
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
        if not self._cpu_executor or not self.bm25_retriever:
            return []
        
        loop = asyncio.get_running_loop()
        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(self._cpu_executor, self.bm25_retriever.invoke, query),
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
        executor = self._cpu_executor or self._search_executor
        
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

