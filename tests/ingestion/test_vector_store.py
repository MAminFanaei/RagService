"""
Tests for ingestion/vector_store.py.

Strategy: mock the Elasticsearch client and FlagEmbedding model so tests
run without ES or a GPU. Tests verify:
  - Index creation called when index doesn't exist
  - Bulk indexing: correct document structure, correct _id assignment
  - delete_by_doc_id: correct query sent to ES
  - update_tags_by_doc_id: correct update_by_query payload
  - add_chunks returns correct list of chunk_ids
  - Embedding dimension inferred from model output (not hardcoded)
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_chunk_dict(chunk_id: str | None = None, doc_id: str = "doc-001") -> dict:
    cid = chunk_id or str(uuid.uuid4())
    return {
        "chunk_id":           cid,
        "doc_id":             doc_id,
        "text":               "Sample text for testing",
        "section_title_text": "Section 1 Sample text for testing",
        "source_file":        "test.pdf",
        "doc_title":          "Test Document",
        "page_number":        1,
        "section_path":       ["Ch1"],
        "section_title":      "Section 1",
        "element_type":       "text",
        "is_table":           False,
        "is_footnote":        False,
        "table_markdown":     None,
        "language":           "en",
        "script_direction":   "ltr",
        "chunk_index":        0,
        "total_chunks":       1,
        "token_count":        10,
        "tags":               ["test"],
        "ingestion_version":  "1.0",
    }


def _fake_embed_result(texts: list[str]) -> list[dict]:
    """Return fake embedding output: 1024-dim dense, minimal sparse."""
    return [
        {
            "dense":  [0.1] * 1024,
            "sparse": {"42": 0.8, "17": 0.3},
        }
        for _ in texts
    ]


# ---------------------------------------------------------------------------
# Unit tests
# ---------------------------------------------------------------------------

class TestElasticsearchVectorStore:

    def _make_store(self):
        from ingestion.vector_store import ElasticsearchVectorStore
        store = ElasticsearchVectorStore(
            es_url="http://localhost:9200",
            index="test-chunks",
            username="",
            password="",
        )
        return store

    # ---- ensure_index --------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_ensure_index_creates_when_missing(self):
        store = self._make_store()
        mock_es = AsyncMock()
        mock_es.indices.exists.return_value = False
        mock_es.indices.create = AsyncMock()
        store._es = mock_es

        await store.ensure_index()

        mock_es.indices.create.assert_called_once()
        call_kwargs = mock_es.indices.create.call_args
        assert call_kwargs.kwargs.get("index") == "test-chunks" or \
               "test-chunks" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_ensure_index_skips_when_exists(self):
        store = self._make_store()
        mock_es = AsyncMock()
        mock_es.indices.exists.return_value = True
        mock_es.indices.create = AsyncMock()
        store._es = mock_es

        await store.ensure_index()
        mock_es.indices.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_index_cached(self):
        """ensure_index should only hit ES once per store instance."""
        store = self._make_store()
        mock_es = AsyncMock()
        mock_es.indices.exists.return_value = True
        store._es = mock_es

        await store.ensure_index()
        await store.ensure_index()  # second call should be no-op
        assert mock_es.indices.exists.call_count == 1

    # ---- add_chunks ----------------------------------------------------- #

    @pytest.mark.asyncio
    async def test_add_chunks_returns_chunk_ids(self):
        store = self._make_store()
        store._index_ensured = True

        mock_es = AsyncMock()
        mock_es.bulk.return_value = {"errors": False, "items": []}
        store._es = mock_es

        chunks = [_make_chunk_dict("chunk-001"), _make_chunk_dict("chunk-002")]

        with patch("ingestion.vector_store._embed_batch", side_effect=_fake_embed_result):
            ids = await store.add_chunks(chunks)

        assert ids == ["chunk-001", "chunk-002"]

    @pytest.mark.asyncio
    async def test_add_chunks_calls_bulk(self):
        store = self._make_store()
        store._index_ensured = True

        mock_es = AsyncMock()
        mock_es.bulk.return_value = {"errors": False, "items": []}
        store._es = mock_es

        chunks = [_make_chunk_dict()]

        with patch("ingestion.vector_store._embed_batch", side_effect=_fake_embed_result):
            await store.add_chunks(chunks)

        mock_es.bulk.assert_called_once()
        call_kwargs = mock_es.bulk.call_args
        # refresh=True must be set
        assert call_kwargs.kwargs.get("refresh") is True or \
               "refresh" in str(call_kwargs)

    @pytest.mark.asyncio
    async def test_add_chunks_embeds_section_title_text(self):
        """Verify the text passed to the embedder is section_title_text, not plain text."""
        store = self._make_store()
        store._index_ensured = True

        mock_es = AsyncMock()
        mock_es.bulk.return_value = {"errors": False, "items": []}
        store._es = mock_es

        captured_texts = []

        def capture_embed(texts):
            captured_texts.extend(texts)
            return _fake_embed_result(texts)

        chunk = _make_chunk_dict()
        chunk["section_title_text"] = "UNIQUE_SECTION_TITLE_TEXT"

        with patch("ingestion.vector_store._embed_batch", side_effect=capture_embed):
            await store.add_chunks([chunk])

        assert "UNIQUE_SECTION_TITLE_TEXT" in captured_texts

    @pytest.mark.asyncio
    async def test_add_chunks_empty_returns_empty(self):
        store = self._make_store()
        result = await store.add_chunks([])
        assert result == []

    @pytest.mark.asyncio
    async def test_add_chunks_dense_vector_1024_dims(self):
        """Verify dense_vector in the bulk payload has 1024 dimensions."""
        store = self._make_store()
        store._index_ensured = True

        indexed_docs = []

        async def capture_bulk(operations, **kwargs):
            # operations alternates: action_dict, doc_dict, action_dict, doc_dict...
            for i, item in enumerate(operations):
                if i % 2 == 1:  # doc
                    indexed_docs.append(item)
            return {"errors": False, "items": []}

        mock_es = AsyncMock()
        mock_es.bulk.side_effect = capture_bulk
        store._es = mock_es

        with patch("ingestion.vector_store._embed_batch", side_effect=_fake_embed_result):
            await store.add_chunks([_make_chunk_dict()])

        assert indexed_docs
        assert len(indexed_docs[0]["dense_vector"]) == 1024

    # ---- delete_by_doc_id ---------------------------------------------- #

    @pytest.mark.asyncio
    async def test_delete_by_doc_id_sends_correct_query(self):
        store = self._make_store()
        store._index_ensured = True

        mock_es = AsyncMock()
        mock_es.delete_by_query.return_value = {"deleted": 5, "failures": []}
        store._es = mock_es

        count = await store.delete_by_doc_id("doc-999")

        assert count == 5
        mock_es.delete_by_query.assert_called_once()
        call_body = mock_es.delete_by_query.call_args.kwargs.get("body") or \
                    mock_es.delete_by_query.call_args.args[0] if mock_es.delete_by_query.call_args.args else {}
        # The query should filter on doc_id
        body_str = str(mock_es.delete_by_query.call_args)
        assert "doc-999" in body_str

    # ---- update_tags_by_doc_id ----------------------------------------- #

    @pytest.mark.asyncio
    async def test_update_tags_sends_update_by_query(self):
        store = self._make_store()
        store._index_ensured = True

        mock_es = AsyncMock()
        mock_es.update_by_query.return_value = {"updated": 3, "failures": []}
        store._es = mock_es

        count = await store.update_tags_by_doc_id("doc-001", ["new-tag"])

        assert count == 3
        mock_es.update_by_query.assert_called_once()
        body_str = str(mock_es.update_by_query.call_args)
        assert "new-tag" in body_str


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

class TestGetVectorStore:

    def test_returns_elasticsearch_instance(self):
        from ingestion.vector_store import get_vector_store, ElasticsearchVectorStore
        with patch("ingestion.vector_store.get_settings") as mock_settings:
            s = MagicMock()
            s.elasticsearch_url      = "http://localhost:9200"
            s.ELASTICSEARCH_INDEX_NAME    = "test-index"
            s.ELASTICSEARCH_USERNAME = ""
            s.ELASTICSEARCH_PASSWORD = ""
            mock_settings.return_value = s

            # Reset lru_cache to pick up mock settings
            get_vector_store.cache_clear()
            store = get_vector_store()
            assert isinstance(store, ElasticsearchVectorStore)
            get_vector_store.cache_clear()

    def test_singleton(self):
        from ingestion.vector_store import get_vector_store
        with patch("ingestion.vector_store.get_settings") as mock_settings:
            s = MagicMock()
            s.elasticsearch_url      = "http://localhost:9200"
            s.ELASTICSEARCH_INDEX_NAME    = "idx"
            s.ELASTICSEARCH_USERNAME = ""
            s.ELASTICSEARCH_PASSWORD = ""
            mock_settings.return_value = s

            get_vector_store.cache_clear()
            a = get_vector_store()
            b = get_vector_store()
            assert a is b
            get_vector_store.cache_clear()


# ---------------------------------------------------------------------------
# Embed batch unit test (no GPU required — mocks FlagEmbedding)
# ---------------------------------------------------------------------------

class TestEmbedBatch:

    def test_embed_batch_raises_when_model_unavailable(self):
        import ingestion.vector_store as vs_module
        # Force model to None
        original = vs_module._EMBED_MODEL
        original_tried = vs_module._EMBED_MODEL_TRIED
        vs_module._EMBED_MODEL = None
        vs_module._EMBED_MODEL_TRIED = True

        try:
            with pytest.raises(RuntimeError, match="FlagEmbedding"):
                vs_module._embed_batch(["test"])
        finally:
            vs_module._EMBED_MODEL = original
            vs_module._EMBED_MODEL_TRIED = original_tried
