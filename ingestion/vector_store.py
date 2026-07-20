"""
Vector store — embedding + Elasticsearch indexing for the ingestion pipeline.

Two classes:
  VectorStoreBackend (ABC)         → interface
  ElasticsearchVectorStore         → production implementation

What this module owns:
  - ES index creation with the correct mapping (dense + sparse vectors)
  - BGE-M3 embedding (dense + sparse) via FlagEmbedding
  - Bulk indexing of approved chunks
  - Deletion by doc_id (used by delete / reindex flows)

What this module does NOT own:
  - Approval logic (that's the review API)
  - SQL updates of vector_id (the task does that after add_chunks returns)

Embedding model: BAAI/bge-m3
  - dense_vector : 1024 dims, cosine similarity
  - sparse_vector: SPLADE-style, stored as ES sparse_vector type

FlagEmbedding is used directly (not via LangChain) because only FlagEmbedding
exposes BGE-M3's sparse output alongside dense.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from abc import ABC, abstractmethod
from functools import lru_cache
from typing import Any

import structlog

from ingestion.config import get_settings

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# ES index mapping
# ---------------------------------------------------------------------------

ES_MAPPING: dict = {
    "mappings": {
        "properties": {
            # Identity
            "chunk_id":  {"type": "keyword"},
            "doc_id":    {"type": "keyword"},
            # Content
            "text":          {"type": "text", "analyzer": "standard"},
            "section_title": {"type": "text", "analyzer": "standard"},
            "section_path":  {"type": "keyword"},
            "element_type":  {"type": "keyword"},
            # Language
            "language":         {"type": "keyword"},
            "script_direction": {"type": "keyword"},
            # Location
            "source_file": {"type": "keyword"},
            "doc_title":   {"type": "keyword"},
            "page_number": {"type": "integer"},
            # Admin
            "tags":              {"type": "keyword"},
            "ingestion_version": {"type": "keyword"},
            # Type flags
            "is_table":    {"type": "boolean"},
            "is_footnote": {"type": "boolean"},
            # Vectors
            "dense_vector": {
                "type": "dense_vector",
                "dims": 1024,
                "index": True,
                "similarity": "cosine",
            },
            "sparse_vector": {"type": "sparse_vector"},
        }
    },
    "settings": {
        "number_of_shards": 1,
        "number_of_replicas": 0,
    },
}

ES_INDEX_SETTINGS: dict = {
    "settings": {
        "number_of_replicas": 0,
    }
}


# ---------------------------------------------------------------------------
# Embedding model (lazy singleton)
# ---------------------------------------------------------------------------

_EMBED_MODEL = None
_EMBED_MODEL_TRIED = False


def _get_embed_model():
    """
    Load BAAI/bge-m3 via FlagEmbedding.
    Returns None if FlagEmbedding is not installed.
    """
    global _EMBED_MODEL, _EMBED_MODEL_TRIED
    if _EMBED_MODEL_TRIED:
        return _EMBED_MODEL
    _EMBED_MODEL_TRIED = True
    try:
        from FlagEmbedding import BGEM3FlagModel  # type: ignore
        _EMBED_MODEL = BGEM3FlagModel(
            "BAAI/bge-m3",
            use_fp16=True,    # half-precision: faster on GPU, fine for search
        )
        logger.info("embed_model_loaded", model="BAAI/bge-m3")
    except Exception as exc:
        logger.error("embed_model_load_failed", error=str(exc))
        _EMBED_MODEL = None
    return _EMBED_MODEL


def _embed_batch(texts: list[str]) -> list[dict]:
    """
    Embed a batch of texts with BGE-M3.
    Returns list of {"dense": [...], "sparse": {...}} dicts.

    Runs synchronously — call via run_in_executor from async context.
    """
    model = _get_embed_model()
    if model is None:
        raise RuntimeError(
            "FlagEmbedding / BAAI/bge-m3 is not available. "
            "Install FlagEmbedding: pip install FlagEmbedding"
        )

    output = model.encode(
        texts,
        batch_size=32,
        max_length=512,
        return_dense=True,
        return_sparse=True,
        return_colbert_vecs=False,
    )

    dense_vecs  = output["dense_vecs"]    # shape: (N, 1024)
    sparse_vecs = output["lexical_weights"]  # list of {token_id: weight}

    results = []
    for d, s in zip(dense_vecs, sparse_vecs):
        # ES sparse_vector format: {token_id_str: float_weight}
        sparse_es = {str(k): float(v) for k, v in s.items() if v > 0}
        results.append({
            "dense":  d.tolist(),
            "sparse": sparse_es,
        })
    return results


# ---------------------------------------------------------------------------
# Abstract interface
# ---------------------------------------------------------------------------

class VectorStoreBackend(ABC):

    @abstractmethod
    async def add_chunks(self, chunks: list[dict]) -> list[str]:
        """
        Embed and index chunks into the vector store.

        Args:
            chunks: Approved chunk dicts (from SQL Chunk records).
                    Must have at least: chunk_id, doc_id, text, section_title_text.

        Returns:
            List of ES document _id values (== chunk_id) in input order.
        """
        ...

    @abstractmethod
    async def delete_by_doc_id(self, doc_id: str) -> int:
        """
        Delete all chunks belonging to doc_id.

        Returns:
            Number of ES documents deleted.
        """
        ...

    @abstractmethod
    async def ensure_index(self) -> None:
        """Create the ES index with correct mapping if it does not exist."""
        ...


# ---------------------------------------------------------------------------
# Elasticsearch implementation
# ---------------------------------------------------------------------------

class ElasticsearchVectorStore(VectorStoreBackend):

    def __init__(
        self,
        es_url: str,
        index: str,
        username: str = "",
        password: str = "",
    ) -> None:
        self._url      = es_url.rstrip("/")
        self._index    = index
        self._username = username
        self._password = password
        self._es       = None        # lazy ES client
        self._index_ensured = False

    def _get_es(self):
        """Lazily create the async Elasticsearch client."""
        if self._es is None:
            from elasticsearch import AsyncElasticsearch  # type: ignore
            kwargs: dict = {"hosts": [self._url]}
            if self._username:
                kwargs["basic_auth"] = (self._username, self._password)
            self._es = AsyncElasticsearch(**kwargs)
        return self._es

    # ------------------------------------------------------------------ #
    # Index management                                                     #
    # ------------------------------------------------------------------ #

    async def ensure_index(self) -> None:
        """Create index with mapping if it does not already exist."""
        if self._index_ensured:
            return
        es = self._get_es()
        try:
            exists = await es.indices.exists(index=self._index)
            if not exists:
                await es.indices.create(index=self._index, body=ES_MAPPING)
                logger.info("es_index_created", index=self._index)
            else:
                logger.debug("es_index_exists", index=self._index)
            self._index_ensured = True
        except Exception as exc:
            logger.error("es_index_creation_failed", index=self._index, error=str(exc))
            raise

    # ------------------------------------------------------------------ #
    # Embedding (runs in thread pool to avoid blocking event loop)        #
    # ------------------------------------------------------------------ #

    async def _embed_async(self, texts: list[str]) -> list[dict]:
        """Embed texts in a thread pool executor."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, _embed_batch, texts)

    # ------------------------------------------------------------------ #
    # Indexing                                                             #
    # ------------------------------------------------------------------ #

    async def add_chunks(self, chunks: list[dict]) -> list[str]:
        """
        Embed and bulk-index chunks into Elasticsearch.

        Steps:
          1. Ensure index exists
          2. Embed section_title_text in batches of 32
          3. Attach dense + sparse vectors to chunk dicts
          4. Bulk index with refresh=True
          5. Return list of _id values (== chunk_id)
        """
        if not chunks:
            return []

        await self.ensure_index()

        # Collect texts to embed (section_title_text preferred, fall back to text)
        texts = [
            (c.get("section_title_text") or c.get("text", "")).strip()
            for c in chunks
        ]

        logger.info("embedding_start", count=len(texts))
        vectors = await self._embed_async(texts)
        logger.info("embedding_complete", count=len(vectors))

        # Build bulk operations
        operations: list[dict] = []
        indexed_ids: list[str] = []

        for chunk, vec in zip(chunks, vectors):
            chunk_id = chunk.get("chunk_id") or str(uuid.uuid4())
            indexed_ids.append(chunk_id)

            # Action line
            operations.append({"index": {"_index": self._index, "_id": chunk_id}})

            # Document — copy all chunk fields, overwrite vectors
            doc = {k: v for k, v in chunk.items()
                   if k not in ("dense_vector", "sparse_vector", "section_title_text",
                                "language_confidence", "parser_name")}
            doc["dense_vector"]  = vec["dense"]
            doc["sparse_vector"] = vec["sparse"]

            # Ensure section_title is indexed (not section_title_text which is embedding input)
            if "section_title" not in doc and chunk.get("section_title"):
                doc["section_title"] = chunk["section_title"]

            operations.append(doc)

        es = self._get_es()
        try:
            resp = await es.bulk(operations=operations, refresh=True)
            if resp.get("errors"):
                failed = [
                    item for item in resp["items"]
                    if item.get("index", {}).get("error")
                ]
                logger.error(
                    "es_bulk_partial_failure",
                    failed_count=len(failed),
                    first_error=failed[0] if failed else None,
                )
        except Exception as exc:
            logger.error("es_bulk_failed", error=str(exc))
            raise

        logger.info(
            "es_bulk_complete",
            index=self._index,
            indexed=len(indexed_ids),
        )
        return indexed_ids

    # ------------------------------------------------------------------ #
    # Deletion                                                             #
    # ------------------------------------------------------------------ #

    async def delete_by_doc_id(self, doc_id: str) -> int:
        """Delete all ES documents where doc_id matches."""
        await self.ensure_index()
        es = self._get_es()
        try:
            resp = await es.delete_by_query(
                index=self._index,
                body={"query": {"term": {"doc_id": doc_id}}},
                refresh=True,
            )
            deleted = resp.get("deleted", 0)
            logger.info("es_delete_by_doc_id", doc_id=doc_id, deleted=deleted)
            return deleted
        except Exception as exc:
            logger.error("es_delete_failed", doc_id=doc_id, error=str(exc))
            raise

    # ------------------------------------------------------------------ #
    # Tag propagation (used by PATCH /documents/{id})                     #
    # ------------------------------------------------------------------ #

    async def update_tags_by_doc_id(self, doc_id: str, tags: list[str]) -> int:
        """Update the `tags` field on all ES chunks belonging to doc_id."""
        await self.ensure_index()
        es = self._get_es()
        try:
            resp = await es.update_by_query(
                index=self._index,
                body={
                    "script": {
                        "source": "ctx._source.tags = params.tags",
                        "params": {"tags": tags},
                    },
                    "query": {"term": {"doc_id": doc_id}},
                },
                refresh=True,
            )
            updated = resp.get("updated", 0)
            logger.info("es_tags_updated", doc_id=doc_id, updated=updated)
            return updated
        except Exception as exc:
            logger.error("es_update_tags_failed", doc_id=doc_id, error=str(exc))
            raise


# ---------------------------------------------------------------------------
# Singleton factory
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def get_vector_store() -> ElasticsearchVectorStore:
    s = get_settings()
    return ElasticsearchVectorStore(
        es_url=s.elasticsearch_url,
        index=s.ELASTICSEARCH_INDEX_NAME,
        username=s.ELASTICSEARCH_USERNAME,
        password=s.ELASTICSEARCH_PASSWORD,
    )
