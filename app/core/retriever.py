
import asyncio
import os
from typing import List, Optional
from concurrent.futures import ThreadPoolExecutor
from langchain_core.documents import Document as LangChainDocument
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder
from langchain_elasticsearch import ElasticsearchStore
import structlog
from app.config import settings

logger = structlog.get_logger()

os.environ["TOKENIZERS_PARALLELISM"] = "false"


def _build_rrf_query(query_text: str, embedding: list[float], k: int) -> dict:
    """
    Build an ES RRF (Reciprocal Rank Fusion) query that combines:
      - kNN semantic search on dense_vector
      - BM25 full-text search on the `text` field

    ES handles the fusion internally — no manual merging needed.
    Requires ES 8.8+ with the RRF feature.
    """
    return {
        "sub_searches": [
            {
                "query": {
                    "match": {"text": query_text}
                }
            },
        ],
        "knn": {
            "field": "dense_vector",
            "query_vector": embedding,
            "k": k,
            "num_candidates": k * 5,
        },
        "rank": {
            "rrf": {
                "window_size": k * 5,
                "rank_constant": 60,
            }
        },
        "size": k,
    }


class Retriever:
    """
    Hybrid retriever with proper async handling.

    ES search runs in a thread pool (similarity_search is sync in langchain-es).
    Optional reranker runs in a separate thread pool.
    BM25 is now ES-native (via USE_RRF toggle) — no in-memory corpus.
    """

    def __init__(
        self,
        es_store: ElasticsearchStore,
        documents: List[LangChainDocument],   # kept for API compat, no longer used
        embeddings: HuggingFaceEmbeddings,
        reranker_model: str,
        output_k: int,
        use_reranker: bool,
        use_bm25: bool,                        # kept for API compat, now a no-op
    ):
        self.es_store    = es_store
        self.embeddings  = embeddings
        self.output_k    = output_k
        self.use_reranker = use_reranker
        # use_bm25 is intentionally ignored — ES native BM25 via USE_RRF replaces it
        self.use_rrf = getattr(settings, "USE_RRF", False)

        self._search_executor = ThreadPoolExecutor(
            max_workers=settings.ELASTIC_SEARCH_WORKERS,
            thread_name_prefix="es_search",
        )

        # Reranker thread pool (only if reranker enabled)
        self._cpu_executor: Optional[ThreadPoolExecutor] = None
        if use_reranker:
            self._cpu_executor = ThreadPoolExecutor(
                max_workers=settings.CPU_BOUNDED_WORKERS,
                thread_name_prefix="cpu_ops",
            )
            logger.info("CPU executor initialized", workers=settings.CPU_BOUNDED_WORKERS)

        if use_reranker:
            self.reranker = CrossEncoder(reranker_model, device=settings.DEVICE)
            logger.info("✓ Reranker enabled", device=settings.DEVICE)
        else:
            self.reranker = None

        if self.use_rrf:
            logger.info("✓ ES RRF hybrid search enabled (kNN + BM25)")
        else:
            logger.info("✓ ES kNN-only search (USE_RRF=False)")

    # ----------------------------------------------------------------------- #
    # Public interface                                                          #
    # ----------------------------------------------------------------------- #

    async def retrieve(self, query: str) -> List[LangChainDocument]:
        """
        Retrieve relevant documents for `query`.

        USE_RRF=False: kNN similarity search only (ES)
        USE_RRF=True:  ES RRF fusion of kNN + BM25 in one query
        Both paths run in a thread pool to avoid blocking the event loop.
        """
        if self.use_rrf:
            results = await self._rrf_search(query, self.output_k * 2)
        else:
            results = await self._es_search_in_executor(query, self.output_k * 2)

        if not results:
            logger.warning("No documents retrieved", query=query[:50])
            return []

        if self.use_reranker and self.reranker:
            return await self._rerank_async(query, results)

        return results[:self.output_k]

    # ----------------------------------------------------------------------- #
    # ES kNN search (original path, USE_RRF=False)                            #
    # ----------------------------------------------------------------------- #

    async def _es_search_in_executor(self, query: str, k: int) -> List[LangChainDocument]:
        """Run ES similarity_search in thread pool (sync method that works)."""
        loop = asyncio.get_running_loop()

        def do_search():
            try:
                return self.es_store.similarity_search(query, k=k)
            except Exception as exc:
                logger.error("ES search error in thread", error=str(exc),
                             error_type=type(exc).__name__)
                return []

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(self._search_executor, do_search),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS,
            )
            return results or []
        except asyncio.TimeoutError:
            logger.warning("ES search timed out")
            return []
        except Exception as exc:
            logger.error("ES search failed", error=str(exc))
            return []

    # ----------------------------------------------------------------------- #
    # ES RRF search (USE_RRF=True)                                            #
    # ----------------------------------------------------------------------- #

    async def _rrf_search(self, query: str, k: int) -> List[LangChainDocument]:
        """
        Run ES RRF query (kNN + BM25 fused) in thread pool.

        Embeds the query first, then sends a single RRF query to ES.
        Falls back to kNN-only if embedding or RRF fails.
        """
        loop = asyncio.get_running_loop()

        def do_rrf():
            try:
                # Embed query
                embedding = self.embeddings.embed_query(query)
                rrf_body = _build_rrf_query(query, embedding, k)

                # Access underlying ES client from ElasticsearchStore
                es_client = self.es_store.client
                index = self.es_store.index_name

                resp = es_client.search(index=index, body=rrf_body)
                hits = resp.get("hits", {}).get("hits", [])

                docs = []
                for hit in hits:
                    src = hit.get("_source", {})
                    docs.append(
                        LangChainDocument(
                            page_content=src.get("text", ""),
                            metadata={
                                k: v for k, v in src.items()
                                if k not in ("text", "dense_vector", "sparse_vector")
                            },
                        )
                    )
                return docs

            except Exception as exc:
                logger.warning(
                    "RRF search failed, falling back to kNN",
                    error=str(exc),
                )
                # Fallback: plain kNN
                try:
                    return self.es_store.similarity_search(query, k=k)
                except Exception as fallback_exc:
                    logger.error("kNN fallback also failed", error=str(fallback_exc))
                    return []

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(self._search_executor, do_rrf),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS,
            )
            return results or []
        except asyncio.TimeoutError:
            logger.warning("RRF search timed out")
            return []
        except Exception as exc:
            logger.error("RRF search failed", error=str(exc))
            return []

    # ----------------------------------------------------------------------- #
    # Reranker                                                                 #
    # ----------------------------------------------------------------------- #

    async def _rerank_async(
        self,
        query: str,
        candidates: List[LangChainDocument],
    ) -> List[LangChainDocument]:
        """Rerank candidates using CrossEncoder in thread pool."""
        if not candidates or not self.use_reranker:
            return candidates[:self.output_k]

        loop = asyncio.get_running_loop()
        executor = self._cpu_executor or self._search_executor

        def rerank():
            pairs = [[query, doc.page_content] for doc in candidates]
            scores = self.reranker.predict(pairs)
            doc_scores = sorted(zip(candidates, scores), key=lambda x: x[1], reverse=True)
            return [doc for doc, _ in doc_scores[:self.output_k]]

        try:
            results = await asyncio.wait_for(
                loop.run_in_executor(executor, rerank),
                timeout=settings.RETRIEVAL_TIMEOUT_SECONDS,
            )
            return results
        except asyncio.TimeoutError:
            logger.warning("Reranking timed out")
            return candidates[:self.output_k]
        except Exception as exc:
            logger.error("Reranking failed", error=str(exc))
            return candidates[:self.output_k]
