# RAG Document Ingestion Pipeline — Complete Implementation Plan

**Project Goal**: Build a production-grade, multilingual (EN/FA/AR) document ingestion pipeline as a standalone microservice that processes uploaded files through parsing → OCR → chunking → embedding → indexing, with human-in-the-loop review before final indexing.

---

## Architecture Decisions & Rationale

### Why a Separate Microservice?

**Decision**: Build `ingestion/` as a standalone FastAPI service (port 8001) in the same repo, sharing MySQL/ES/Redis with the main app.

**Reasons**:
1. **Different lifecycle**: Main app runs 24/7 serving users; ingestion runs on-demand when documents are added
2. **Resource isolation**: OCR/parsing are GPU/CPU-heavy; separating prevents impacting user query latency
3. **Independent deployment**: Update ingestion logic without restarting the user-facing service
4. **Easy future separation**: All ingestion code in one directory, shares DB/ES via configuration — one config change makes it fully independent

**Access pattern**: Nginx routes `/ingestion/*` → `localhost:8001`, main app stays on `/api/*`

---

### Why Strict Approval Workflow?

**Decision**: Documents go through `PENDING → PROCESSING → REVIEW → INDEXING → READY`. Admin must explicitly approve before chunks hit Elasticsearch.

**Reasons**:
1. **Quality control**: OCR on RTL documents produces errors; manual review catches them before bad data enters the RAG system
2. **Cost control**: Embeddings and ES indexing are expensive; don't waste resources on garbage chunks
3. **Regulatory compliance**: For legal/medical domains, human verification is often required
4. **Iterative improvement**: Review feedback identifies which parsers/OCR engines need tuning

**Alternative considered**: Auto-index everything → worse data quality, harder to debug retrieval failures

---

### Why This Chunk Schema?

**Decision**: Simple, flat schema with no language-specific ES analyzers, language stored as metadata only.

```python
{
    "chunk_id": "uuid",
    "doc_id": "uuid",
    "text": "the chunk text",  # standard analyzer, no .fa/.ar sub-fields
    "section_title_text": "title prepended",  # this gets embedded
    "language": "fa",  # metadata keyword, NOT used for search routing
    "script_direction": "rtl",
    "page_number": 3,
    "section_path": ["Ch1", "1.2"],
    "element_type": "text",
    "is_table": False,
    "tags": ["legal"],
    # ... other metadata
}
```

**Reasons**:
1. **Simplicity**: Standard analyzer works for all languages; BGE-M3 embedding model already handles multilingual semantic search
2. **Avoid premature optimization**: Language-specific analyzers (Persian/Arabic stemmers) add complexity; test if the simple approach works first
3. **Flexibility**: Language field lets you add language-aware features later without schema migration
4. **Fewer failure modes**: No analyzer routing logic that can break; one search path for all languages

**Alternative considered**: Per-language sub-fields (`text.fa`, `text.ar`) → adds complexity, benefits unclear until tested

---

### Why This Database Design?

**Decision**: `ingestion/database.py` reuses `async_engine` from `app/core/database.py`, creates own session factory.

```python
# ingestion/database.py
from app.core.database import Base, async_engine  # Shared

AsyncSessionLocal = async_sessionmaker(bind=async_engine, ...)
```

**Reasons**:
1. **Shared connection pool**: One engine = efficient connection reuse, no wasted connections
2. **Single source of truth**: Same `Base.metadata` means Alembic sees all tables in one place
3. **Easy separation**: To separate later, just create own engine with different `DATABASE_URL`
4. **Matches async pattern**: Both services use async SQLAlchemy, share the driver

**Why NOT import session factory from app?** Ingestion needs its own `get_db()` dependency to make the service independently deployable.

---

### Why Independent Config?

**Decision**: `ingestion/config.py` is a completely separate `IngestionSettings` class that reads from `.env` directly.

**Reasons**:
1. **No import coupling**: Ingestion doesn't import `app/config.py` → can be moved to separate repo cleanly
2. **Override flexibility**: Can point to different `.env` file or different DB/ES instances without touching app config
3. **Clear ownership**: Ingestion settings live with ingestion code

**Trade-off**: Some duplication (DATABASE_URL appears in both configs) — acceptable for clean separation

---

### Why This BM25 Approach?

**Decision**: Remove in-memory `BM25Retriever`, use ES native BM25 (it's already built-in), add optional RRF via `USE_RRF` toggle.

**Current problem**: 
- BM25 index rebuilt from JSON files at startup → stale when new docs added
- Separate in-memory corpus → memory overhead, out of sync with ES
- No multilingual awareness

**New approach**:
```python
USE_RRF = False  # default: ES kNN only (current behavior minus BM25)
USE_RRF = True   # optional: ES native RRF (kNN + BM25 fused in one query)
```

**Reasons**:
1. **BM25 is free in ES**: Every `text` field is BM25-indexed automatically (ES default)
2. **Always in sync**: Same datastore for semantic + lexical search
3. **Less code**: Remove entire BM25Retriever class, CPU executor, manual merge logic
4. **Better fusion**: ES RRF is tuned and tested; manual merging is error-prone
5. **Toggleable**: Can disable RRF, just use kNN (simpler), or enable for hybrid search

**Why keep it optional?** Let users A/B test retrieval quality before committing.

---

## Project Structure

```
repo_root/
├── app/                          # existing main service (minimal changes)
│   └── core/retriever.py         # Phase 4: remove BM25Retriever, add USE_RRF toggle
│
├── ingestion/                    # new microservice (all phases)
│   ├── main.py
│   ├── config.py
│   ├── database.py
│   ├── models.py                 # Document + Chunk tables
│   ├── storage.py                # LocalStorage + MinIO
│   ├── parsers/
│   │   ├── base.py
│   │   ├── router.py
│   │   ├── docx.py
│   │   ├── pdf_pymupdf.py        # fallback
│   │   ├── pdf_docling.py        # primary for text PDFs
│   │   ├── pdf_deepdoc.py        # primary for scanned PDFs
│   │   ├── pptx.py
│   │   ├── xlsx.py
│   │   ├── html.py
│   │   └── text.py
│   ├── ocr/
│   │   ├── base.py
│   │   ├── router.py
│   │   ├── dots.py               # primary
│   │   ├── deepdoc.py            # fallback
│   │   └── gemma.py              # last resort
│   ├── chunkers/
│   │   ├── base.py
│   │   ├── router.py
│   │   ├── fixed.py              # RecursiveCharacterTextSplitter
│   │   ├── heading.py            # heading-aware grouping
│   │   ├── semantic.py           # Chonkie semantic
│   │   ├── sentence.py           # Chonkie sentence
│   │   └── hierarchical.py       # parent-child chunks
│   ├── pipeline.py               # orchestrator: combines all steps
│   ├── intake.py                 # MIME, hash, dedup, storage
│   ├── normalizer.py             # ParsedElement → ChunkDict
│   ├── dedup.py                  # exact + near-dup + quality filters
│   ├── vector_store.py           # ES indexing + embedding
│   ├── tasks/
│   │   ├── worker.py             # Celery app
│   │   └── ingestion_task.py     # process_document + index_document tasks
│   └── api/
│       ├── upload.py             # POST /documents, POST /documents/bulk
│       ├── review.py             # GET/PATCH/DELETE chunks, POST approve
│       ├── documents.py          # GET/DELETE/PATCH documents, GET metrics
│       └── tasks.py              # GET /tasks/{id}
│
├── tests/ingestion/
│   ├── test_storage.py
│   ├── test_models.py
│   ├── test_intake.py
│   ├── test_parsers.py
│   ├── test_ocr.py
│   ├── test_normalizer.py
│   ├── test_dedup.py
│   ├── test_chunkers.py
│   ├── test_vector_store.py
│   ├── test_pipeline.py
│   └── test_api.py
│
├── alembic/
│   └── env.py                    # add: from ingestion.models import Document, Chunk
│
├── nginx.conf                    # add /ingestion/ → localhost:8001
└── .env                          # shared config
```

---

## Shared Data Contract

### Elasticsearch Chunk Schema

**What gets indexed** (written by ingestion, read by main app retriever):

```python
{
    # Identity
    "chunk_id": "uuid-string",       # ES document _id
    "doc_id": "uuid-string",

    # Content
    "text": "actual chunk text",     # standard analyzer
    "section_title_text": "Section 1.2 actual chunk text",  # embedded, not indexed

    # Location
    "source_file": "contract.pdf",
    "doc_title": "Annual Report 2024",
    "page_number": 3,
    "section_path": ["Chapter 1", "1.2 Background"],
    "section_title": "1.2 Background",

    # Type
    "element_type": "text",  # text|table|heading|footnote|caption|code|list_item|formula
    "is_table": False,
    "table_markdown": None,

    # Language (metadata only)
    "language": "fa",  # en|fa|ar|mixed|unknown
    "script_direction": "rtl",

    # Stats
    "chunk_index": 0,
    "total_chunks": 5,
    "token_count": 245,

    # Admin
    "tags": ["legal", "Q4-2024"],
    "ingestion_version": "1.0",

    # Vectors
    "dense_vector": [0.123, ...],    # 1024 dims, BGE-M3
    "sparse_vector": {...}            # SPLADE, BGE-M3
}
```

**ES mapping**:

```json
{
  "mappings": {
    "properties": {
      "chunk_id": {"type": "keyword"},
      "doc_id": {"type": "keyword"},
      "text": {"type": "text", "analyzer": "standard"},
      "section_title": {"type": "text", "analyzer": "standard"},
      "section_path": {"type": "keyword"},
      "element_type": {"type": "keyword"},
      "language": {"type": "keyword"},
      "page_number": {"type": "integer"},
      "tags": {"type": "keyword"},
      "dense_vector": {
        "type": "dense_vector",
        "dims": 1024,
        "index": true,
        "similarity": "cosine"
      },
      "sparse_vector": {"type": "sparse_vector"}
    }
  }
}
```

**Why this schema?**
- `text` field with standard analyzer: Works for all languages, BM25-indexed by default
- No `.fa`/`.ar` sub-fields: Simpler, BGE-M3 handles multilingual semantics
- `sparse_vector`: Enables future lexical+semantic hybrid via RRF
- `section_path` as keyword array: Enables filter by document section
- `language` as metadata: Can add language-aware features later without reindexing

---

### SQL Schema

**Document table** (`ingestion_documents`):

```sql
id                  VARCHAR(36) PRIMARY KEY
original_filename   VARCHAR(512) NOT NULL
mime_type           VARCHAR(128) NOT NULL
file_size_bytes     BIGINT NOT NULL
content_hash        VARCHAR(64) NOT NULL UNIQUE INDEX  -- SHA-256 for dedup
storage_backend     VARCHAR(32) DEFAULT 'local'
storage_path        VARCHAR(1024) NOT NULL
status              ENUM(...) DEFAULT 'PENDING' INDEX
error_message       TEXT
total_pages         INTEGER
total_chunks        INTEGER
detected_language   VARCHAR(16)
parser_used         VARCHAR(64)
ocr_used            VARCHAR(64)
tags                JSON
custom              JSON
ingestion_version   VARCHAR(16) DEFAULT '1.0'
created_at          DATETIME(timezone=True)
updated_at          DATETIME(timezone=True)
processed_at        DATETIME(timezone=True)
```

**Status enum**: `PENDING | PROCESSING | REVIEW | INDEXING | READY | FAILED`

**Chunk table** (`ingestion_chunks`):

```sql
id                  VARCHAR(36) PRIMARY KEY
doc_id              VARCHAR(36) FK → ingestion_documents ON DELETE CASCADE
page_number         INTEGER
page_range          JSON                  -- [start, end]
bounding_box        JSON                  -- {x0,y0,x1,y1}
section_title       VARCHAR(512)
section_path        JSON                  -- ["Ch1", "1.2"]
heading_level       INTEGER
element_type        VARCHAR(32) DEFAULT 'text'
is_footnote         BOOLEAN DEFAULT FALSE
is_table            BOOLEAN DEFAULT FALSE
table_markdown      TEXT
text                TEXT NOT NULL
char_count          INTEGER NOT NULL
token_estimate      INTEGER
language            VARCHAR(16)
script_direction    VARCHAR(4)
chunk_index         INTEGER NOT NULL DEFAULT 0
total_chunks        INTEGER
vector_id           VARCHAR(128) INDEX    -- ES document _id after indexing
approved            BOOLEAN DEFAULT FALSE
edited_by_admin     BOOLEAN DEFAULT FALSE
created_at          DATETIME(timezone=True)
```

**Why these fields?**
- `content_hash` on Document: Deduplication before processing (save compute)
- `approved` on Chunk: Enables review workflow (strict approval)
- `vector_id`: Links SQL chunk to ES document (enables delete, reindex)
- `edited_by_admin`: Audit trail for human corrections
- Bidirectional relationship with `lazy="noload"`: Safe for async, matches existing User model pattern

---

## Configuration

**Add to `app/config.py`** (or create `ingestion/config.py` as independent copy):

```python
# Storage
STORAGE_BACKEND: str = "local"  # "local" | "minio"
LOCAL_STORAGE_BASE_DIR: str = "./data/uploads"
MINIO_ENDPOINT: str = ""
MINIO_ACCESS_KEY: str = ""
MINIO_SECRET_KEY: str = ""
MINIO_BUCKET: str = "rag-documents"

# Ingestion
MAX_UPLOAD_FILE_SIZE_MB: int = 100
INGESTION_VERSION: str = "1.0"

# OCR
DOTS_OCR_API_BASE: str = ""              # vLLM endpoint, empty = skip dots.ocr
OLLAMA_BASE_URL: str = "http://localhost:11434"
GEMMA_OCR_MODEL: str = "gemma2:27b"

# Celery
CELERY_BROKER_URL: str = ""              # defaults to REDIS_URL
CELERY_RESULT_BACKEND: str = ""
CELERY_WORKER_CONCURRENCY: int = 1       # one GPU-heavy task at a time

# Chunking
CHUNKING_STRATEGY: str = "auto"          # auto|heading|semantic|sentence|fixed|hierarchical
CHUNK_MIN_TOKENS: int = 50
CHUNK_MAX_TOKENS: int = 512
CHUNK_OVERLAP_TOKENS: int = 50

# Quality
DEDUP_SIMILARITY_THRESHOLD: float = 0.85
MIN_CHUNK_WORD_COUNT: int = 8
LANGUAGE_DETECTION_CONFIDENCE_THRESHOLD: float = 0.8

# Retriever (main app)
USE_RRF: bool = False                    # toggle ES RRF hybrid search
```

**Why separate these?** Ingestion service can read from different config source later by just changing `env_file` path.

---

## PHASE 1: Foundation & File Intake

**Goal**: Set up the ingestion service skeleton, database models, file storage abstraction, and file upload with deduplication.

### 1.1 — Database Models & Migration

**Files**: `ingestion/models.py`, `ingestion/database.py`

**`ingestion/database.py`**:
```python
from app.core.database import Base, async_engine
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

# Own session factory, shared engine
AsyncSessionLocal = async_sessionmaker(
    bind=async_engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
```

**Why this design?** Reuses connection pool, shares `Base`, but ingestion has own session lifecycle.

**`ingestion/models.py`**:
- Define `DocumentStatus` enum inline (matches User model pattern)
- Define `Document` model with all fields from schema above
- Define `Chunk` model with all fields from schema above
- UUID default: `default=lambda: str(uuid.uuid4())` (matches User model)
- Bidirectional relationship: `Document.chunks ↔ Chunk.document`, `lazy="noload"`

**Alembic**: Add to `alembic/env.py`:
```python
from ingestion.models import Document, Chunk  # noqa: F401
```

Run: `alembic revision --autogenerate -m "add ingestion models"` then `alembic upgrade head`

---

### 1.2 — Storage Abstraction

**File**: `ingestion/storage.py`

**Three classes in one file**:

```python
class StorageBackend(ABC):
    @abstractmethod
    async def save(self, data: bytes, path: str) -> str: ...
    @abstractmethod
    async def load(self, path: str) -> bytes: ...
    @abstractmethod
    async def delete(self, path: str) -> None: ...
    @abstractmethod
    async def exists(self, path: str) -> bool: ...

class LocalStorage(StorageBackend):
    # Uses aiofiles, path traversal protection

class MinIOStorage(StorageBackend):
    # Uses miniopy-async, fully implemented but not tested

@lru_cache(1)
def get_storage() -> StorageBackend:
    if settings.STORAGE_BACKEND == "local":
        return LocalStorage(settings.LOCAL_STORAGE_BASE_DIR)
    elif settings.STORAGE_BACKEND == "minio":
        return MinIOStorage(...)
    raise ValueError(...)
```

**Why one file?** ABC + implementations are small, always used together.

---

### 1.3 — Main App & Health Endpoint

**File**: `ingestion/main.py`

```python
app = FastAPI(
    title="RAG Ingestion Service",
    version="1.0.0",
    root_path="/ingestion"  # Nginx strips prefix
)

@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "db": await check_db(),
        "es": await check_es(),
        "redis": await check_redis(),
    }
```

**Why `root_path`?** Makes OpenAPI docs work correctly behind `/ingestion/` prefix.

---

### 1.4 — File Intake

**File**: `ingestion/intake.py`

**Function**: `async def intake_file(data: bytes, filename: str, tags: list[str] | None) -> dict`

**Steps**:
1. Detect true MIME from bytes via `python-magic` (not extension)
2. Check against `MIME_TO_PARSER` allowlist → 415 if unknown
3. Check size > `MAX_UPLOAD_FILE_SIZE_MB` → 413 if too large
4. Compute SHA-256 of raw bytes
5. Query `Document` by `content_hash`:
   - Found + `status=READY` → return `{status: "duplicate", doc_id}`
   - Found + `status=FAILED` → delete old, continue
   - Not found → continue
6. Generate `doc_id` (UUID)
7. Build path: `{year}/{month}/{doc_id}/{filename}` (year/month from `datetime.now()`)
8. `await storage.save(data, path)`
9. Create `Document(status=PENDING, ...)`
10. `await db.commit()`
11. Return `{doc_id, mime_type, storage_path}`

**MIME allowlist**:
```python
MIME_TO_PARSER = {
    "application/pdf": "pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": "pptx",
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
    "text/html": "html",
    "text/plain": "text",
    "text/markdown": "text",
    "image/png": "ocr_direct",
    "image/jpeg": "ocr_direct",
    "image/tiff": "ocr_direct",
}
```

**Why hash-based dedup?** Identical file uploaded twice wastes OCR/parsing compute. Hash check is cheap.

---

### Phase 1 Tests

**File**: `tests/ingestion/test_storage.py`
- LocalStorage: save, load, delete, exists
- Path traversal blocked

**File**: `tests/ingestion/test_models.py`
- Both models import
- Enum values correct
- Relationship navigation works

**File**: `tests/ingestion/test_intake.py`
- Valid PDF accepted, DB record created, file on disk
- Unknown MIME → 415
- Oversized → 413
- Duplicate file → second returns duplicate status

---

## PHASE 2: Parsing, OCR, Normalization & Quality

**Goal**: Transform any uploaded file into a clean, normalized list of chunk dictionaries ready for chunking.

### 2.1 — Parser Architecture

**Internal type** (before normalization):

```python
@dataclass
class ParsedElement:
    text: str
    element_type: str              # text|table|heading|footnote|caption|code
    page_number: int | None
    bounding_box: dict | None      # {x0,y0,x1,y1}
    section_title: str | None
    section_path: list[str]        # ["Ch1", "1.2", "1.2.3"]
    heading_level: int | None      # 1-6
    is_table: bool
    table_markdown: str | None
    language_hint: str | None
    parser_name: str
    raw_metadata: dict             # parser-specific debug info
```

**Why this type?** Parsers output different schemas; this is the unification layer before normalization.

**Parser ABC** (`parsers/base.py`):

```python
class BaseParser(ABC):
    @abstractmethod
    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]: ...
    
    @property
    @abstractmethod
    def name(self) -> str: ...
```

---

### 2.2 — Parser Router & Fallback

**File**: `parsers/router.py`

**Function**: `async def route_and_parse(mime_type: str, data: bytes, filename: str) -> list[ParsedElement]`

**Logic**:
1. Map MIME to parser priority list (primary + fallbacks)
2. For PDFs: detect scanned pages via `is_page_scanned(page)` first
3. Try each parser in order, catch exceptions, log failures
4. Return first successful result
5. If all fail → raise with full error chain

**PDF routing**:
```python
if is_text_pdf(data):
    parsers = [DoclingParser(), PyMuPDFParser()]
elif is_scanned_pdf(data):
    parsers = [DeepDocParser(), PyMuPDFParser()]  # + OCR later
else:  # mixed
    parsers = [DoclingParser(), DeepDocParser(), PyMuPDFParser()]
```

**Scanned page detection**:
```python
def is_page_scanned(page) -> bool:
    text = page.get_text("text").strip()
    images = page.get_images()
    return len(text) == 0 and len(images) > 0
```

**Why fallback chain?** Parsers fail on edge cases; fallback ensures robustness.

---

### 2.3 — DOCX Parser

**File**: `parsers/docx.py`

**Fixes from old pipeline**:
- Bug: h7/h8 overwriting h6 key → fixed with correct dict keys
- Bug: duplicate `elif lvl == 6` → fixed with lvl 7, 8
- Bug: grouping by `(doc_id, section_id)` where doc_id is None → removed doc_id from key

**Extracts**:
- Paragraph styles → heading level (regex match `Heading N`)
- Builds `section_path` breadcrumb from heading hierarchy
- Tables → Markdown via `tabulate` library
- Footnotes → via XML namespace `w:footnote`
- Document properties (title, author, created, modified)

**Limitation**: No page numbers (DOCX doesn't store them) → `page_number=None`

**Why python-docx?** Only library with full access to DOCX XML structure (styles, footnotes).

---

### 2.4 — PDF Parsers

**`parsers/pdf_pymupdf.py`** — Always-available fallback:
- `PyMuPDF` for text + bounding boxes + font sizes
- `PyMuPDF4LLM` for Markdown output
- Returns image pages with `raw_metadata["image_bytes"]` → signals OCR needed
- **Why?** No dependencies on ML models, fastest, never fails

**`parsers/pdf_docling.py`** — Primary for text PDFs:
- `docling` library (DocLayNet model for layout)
- Extracts: reading order, tables (TableFormer), footnotes, figure captions
- Outputs structured elements (not raw Markdown)
- **Why?** Best layout analysis for complex documents

**`parsers/pdf_deepdoc.py`** — Primary for scanned PDFs:
- Clone `deepdoc/` from RAGFlow repo
- `deepdoc/parser/pdf_parser.py` handles layout + OCR + TSR
- Auto-rotation: tries 4 angles, picks highest OCR confidence
- **Why?** Production-proven for scanned documents, handles rotation

---

### 2.5 — Other Parsers

**`parsers/pptx.py`**: `python-pptx`, slide number = page number, speaker notes

**`parsers/xlsx.py`**: `openpyxl`, each sheet = section, tables to Markdown

**`parsers/html.py`**: `trafilatura` (main content extraction) + `BeautifulSoup4` (heading structure)

**`parsers/text.py`**: Direct file read for `.txt`, `MarkdownHeaderTextSplitter` for `.md`

---

### 2.6 — OCR Layer

**When invoked**: Parser returns `ParsedElement` with `element_type="image_page"` and `raw_metadata["image_bytes"]`.

**OCR result type**:

```python
@dataclass
class OCRResult:
    text: str
    confidence: float | None
    language_detected: str | None
    structured_elements: list[ParsedElement] | None  # if engine returns layout
    engine_name: str
```

**OCR ABC** (`ocr/base.py`):

```python
class BaseOCR(ABC):
    @abstractmethod
    async def run(self, image_bytes: bytes, language_hint: str | None) -> OCRResult: ...
    
    @property
    @abstractmethod
    def name(self) -> str: ...
```

**Router** (`ocr/router.py`):

```python
async def route_and_ocr(image_bytes: bytes, language_hint: str | None) -> OCRResult:
    engines = [DotsOCR(), DeepDocOCR(), GemmaOCR()]
    for engine in engines:
        result = await engine.run(image_bytes, language_hint)
        if is_valid(result):  # no repetition, no corruption, not empty
            return result
    raise AllOCRFailedError(...)
```

**Failure detection**:
- Repetition: same 5-word sequence > 3 times
- Corruption: >30% non-alphanumeric chars
- Empty: obvious

**Why this matters?** dots.ocr sometimes outputs repetition artifacts on special characters; fallback catches this.

---

**`ocr/dots.py`** — Primary:
- Calls vLLM endpoint (OpenAI-compatible) at `DOTS_OCR_API_BASE`
- Input: 200 DPI image via `page.get_pixmap(dpi=200)`
- Prompt: `prompt_layout_all_en`
- Output: Markdown → parse into `ParsedElement` list
- **Why?** Fastest, multilingual, structured output

**`ocr/deepdoc.py`** — Fallback:
- `deepdoc/vision/` cloned from RAGFlow
- `OCR` + `LayoutRecognizer` + `TableStructureRecognizer`
- Runs locally (GPU or CPU)
- **Why?** Self-hosted, no API dependency, proven quality

**`ocr/gemma.py`** — Last resort:
- Ollama API, Gemma 2 27B
- Prompt: "Extract all text. Preserve structure. RTL text: preserve right-to-left reading order."
- **Why?** Highest quality, handles edge cases, but slowest

---

### 2.7 — Normalization

**File**: `ingestion/normalizer.py`

**Function**: `def normalize(elements: list[ParsedElement], doc: Document) -> list[dict]`

**Steps per element**:
1. Assign `chunk_id` (UUID)
2. Attach `doc_id`, `source_file`, `ingestion_version` from `doc`
3. Run language detection via `lingua-py` on `text`
4. Set `script_direction`: `"rtl"` if language in `{fa, ar, ur, he}`, else `"ltr"`
5. Apply bounding box heuristics (if no semantic label):
   - Footnote: `font_size < 9 AND y > page_height * 0.85`
   - Header: `y < page_height * 0.08`
   - Footer: `y > page_height * 0.92`
6. If RTL page: sort bounding boxes right-to-left within each y-band
7. Build `section_title_text = section_title + " " + text`
8. Compute `char_count = len(text)`, `token_estimate` via tiktoken
9. Return dict matching chunk contract schema

**Language detection**:
- `lingua-py` (more accurate than `langdetect` for short RTL text)
- Per-element, not per-document
- Confidence < threshold → `language="unknown"`
- Multiple languages detected → `language="mixed"`

**Why `section_title_text`?** Prepending section title to text before embedding improves retrieval precision (cite: LlamaIndex best practices).

---

### 2.8 — Deduplication & Quality

**File**: `ingestion/dedup.py`

**Function**: `def filter_chunks(chunks: list[dict]) -> list[dict]`

**Filters applied**:

1. **Exact dedup**: SHA-256 of `text.lower().strip()` → within same doc, skip duplicates
2. **Near-dedup**: MinHash LSH (threshold 0.85) → removes repeated boilerplate
3. **Word count**: discard if `len(text.split()) < MIN_CHUNK_WORD_COUNT`
4. **Fragmentation**: discard if `newlines / total_chars > 0.30`
5. **Noise**: discard if `non_alphanum / total_chars > 0.90`
6. **Language confidence**: discard if `language="unknown"` AND confidence low
7. **Empty**: discard if `text.strip() == ""`

**Never filtered**:
- `is_table=True` → tables always kept
- `element_type="heading"` → headings always kept (metadata carriers)

**Why these filters?** OCR produces noise; quality filters prevent garbage from entering the vector store.

---

### Phase 2 Tests

**File**: `tests/ingestion/test_parsers.py`
- Each parser: known-good file → element count > 0, section_path present, tables detected
- DOCX: test with actual RTL DOCX files
- PDF router: fallback chain works

**File**: `tests/ingestion/test_ocr.py`
- Each engine: English, Persian, Arabic images
- Fallback on forced failure
- RTL text preserved

**File**: `tests/ingestion/test_normalizer.py`
- Persian text → `language="fa"`, `script_direction="rtl"`
- Arabic text → `language="ar"` (not `"fa"`)
- All contract fields present

**File**: `tests/ingestion/test_dedup.py`
- Exact dup removed
- Table kept despite short text
- Garbled OCR removed

---

## PHASE 3: Chunking, Embedding & Indexing

**Goal**: Split normalized elements into chunks, embed them, index into Elasticsearch.

### 3.1 — Chunker Architecture

**Chunker ABC** (`chunkers/base.py`):

```python
class BaseChunker(ABC):
    @abstractmethod
    def chunk(self, elements: list[dict]) -> list[dict]: ...
    
    @property
    @abstractmethod
    def name(self) -> str: ...
```

**Router** (`chunkers/router.py`):

```python
def select_chunker(doc: Document, elements: list[dict]) -> BaseChunker:
    if settings.CHUNKING_STRATEGY != "auto":
        return get_chunker_by_name(settings.CHUNKING_STRATEGY)
    
    # Auto-selection logic:
    if has_heading_structure(elements):
        return HeadingChunker()
    elif doc.parser_used in ["deepdoc", "dots_ocr"]:  # OCR output
        return SemanticChunker()
    elif doc.total_pages > 50:
        return HierarchicalChunker()
    else:
        return FixedChunker()
```

**Table handling** (applies to ALL chunkers):
- Before chunking: extract all `is_table=True` elements
- Pass through as atomic chunks (never split)
- Re-insert at correct position in output

**Why auto-selection?** Different documents need different strategies; auto-selection optimizes per document type.

---

### 3.2 — Chunker Implementations

**`chunkers/fixed.py`** — Baseline:
- `RecursiveCharacterTextSplitter` from LangChain
- Separators: `["\n\n", "\n", ".", " ", ""]`
- Respects `CHUNK_MAX_TOKENS`, `CHUNK_OVERLAP_TOKENS`

**`chunkers/heading.py`** — For structured docs:
- Group elements by `section_path`
- If group > max tokens → split within group, inherit heading metadata
- If group < min tokens → merge with next sibling

**`chunkers/semantic.py`** — For OCR output:
- `Chonkie SemanticChunker` with BGE-M3 embeddings
- Splits on semantic similarity drops
- Each chunk inherits nearest heading metadata

**`chunkers/sentence.py`** — For slides/short docs:
- `Chonkie SentenceChunker`
- Multilingual sentence tokenizer
- Overlap in sentences, not characters

**`chunkers/hierarchical.py`** — For long docs:
- `LlamaIndex HierarchicalNodeParser`
- Parent (1024 tokens) + child (256 tokens)
- Adds `parent_chunk_id` field to contract
- **Use case**: Retrieve small chunk for precision, expand to parent for LLM context

---

### 3.3 — Embedding

**Where**: Inside `ingestion/vector_store.py`, not a separate file.

**Library**: `FlagEmbedding` (not `langchain_huggingface`)
- Reason: Direct access to BGE-M3's dense + sparse + ColBERT outputs

**What gets embedded**: `section_title_text` field (title prepended to text)

**Batching**: 32 chunks per batch (avoid OOM on 8GB GPU)

**Async pattern**: `asyncio.get_running_loop().run_in_executor(None, model.encode, texts)`

**Dimension**: Inferred from first call (no hardcoding)

---

### 3.4 — Elasticsearch Indexing

**File**: `ingestion/vector_store.py`

**Two classes**:
```python
class VectorStoreBackend(ABC):
    @abstractmethod
    async def add_chunks(self, chunks: list[dict]) -> list[str]: ...
    @abstractmethod
    async def delete_by_doc_id(self, doc_id: str) -> int: ...

class ElasticsearchVectorStore(VectorStoreBackend):
    # Implementation

@lru_cache(1)
def get_vector_store() -> VectorStoreBackend:
    return ElasticsearchVectorStore(...)
```

**Write path**:
1. Receive approved chunks (where `approved=True`)
2. For each chunk: embed `section_title_text` → dense + sparse vectors
3. Build ES bulk operations with both vectors + all metadata fields
4. Execute `await es.bulk(operations, refresh=True)`
5. Store returned `_id` as `vector_id` in SQL Chunk record

**Index creation**: On first write, check index exists, create with mapping if not. Dimension read from first embedding output.

**Why `refresh=True`?** Ensures chunks are immediately searchable (trade latency for consistency).

---

### Phase 3 Tests

**File**: `tests/ingestion/test_chunkers.py`
- Each chunker: output within token bounds, metadata preserved, tables not split
- Semantic: test with Persian paragraph

**File**: `tests/ingestion/test_vector_store.py`
- Index 10 chunks → verify in ES
- Search known text → retrieved
- `delete_by_doc_id` → only those chunks removed
- Dimension matches BGE-M3 output

---

## PHASE 4: Task System, API & Observability

**Goal**: Celery workers for background processing, full HTTP API for upload/review/approve, structured logging and metrics.

### 4.1 — Celery Task System

**Files**: `ingestion/tasks/worker.py`, `ingestion/tasks/ingestion_task.py`

**Worker config**:
```python
celery_app = Celery(
    "ingestion",
    broker=settings.CELERY_BROKER_URL or settings.REDIS_URL,
    backend=settings.CELERY_RESULT_BACKEND or settings.REDIS_URL,
)
celery_app.conf.update(
    task_acks_late=True,           # task re-queued on worker crash
    task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1,  # one GPU-heavy task at a time
    task_serializer="json",
    result_expires=86400,
)
```

**Two tasks**:

**Task 1** — `process_document(doc_id: str)`:
```python
1. Load Document, set status=PROCESSING
2. Load file from storage
3. Route to parser → list[ParsedElement]
4. If image pages: route to OCR
5. Normalize → list[dict]
6. Dedup + quality filters
7. Route to chunker → final list[dict]
8. Save to SQL Chunk table with approved=False
9. Set Document status=REVIEW
10. Return {doc_id, chunk_count}
```

**Task 2** — `index_document(doc_id: str)`:
```python
1. Load Document, set status=INDEXING
2. Load chunks where approved=True
3. Embed + index into ES
4. Update chunk.vector_id for each
5. Set Document status=READY
6. Return {doc_id, indexed_count}
```

**Retry strategy**:
```python
except Exception as exc:
    if self.request.retries < 3:
        raise self.retry(exc=exc, countdown=60 * (2 ** self.request.retries))
    else:
        doc.status = FAILED
        doc.error_message = str(exc)
        db.commit()
```

**Why two tasks?** Review workflow requires human approval before indexing.

---

### 4.2 — HTTP API

**All endpoints under `/ingestion/` via Nginx. JWT auth reused from main app (check `is_admin=True`).**

**`api/upload.py`**:

```
POST /documents
    Content-Type: multipart/form-data
    Body: file, tags (optional), chunking_strategy (optional)
    Returns: {doc_id, task_id, status: "queued"}
    
    Flow:
    1. Call intake_file(data, filename, tags)
    2. Dispatch process_document.delay(doc_id)
    3. Return immediately

POST /documents/bulk
    Content-Type: multipart/form-data
    Body: files[]
    Returns: [{filename, doc_id, task_id, status}, ...]
    
    Per-file errors included, batch never fails entirely
```

**`api/review.py`**:

```
GET /documents/{doc_id}/chunks?page=1&page_size=50
    Returns: paginated Chunk list with full metadata
    
PATCH /documents/{doc_id}/chunks/{chunk_id}
    Body: {text, element_type, section_title}
    Sets: edited_by_admin=True
    Returns: updated Chunk

DELETE /documents/{doc_id}/chunks/{chunk_id}
    Removes from SQL only (not in ES yet at REVIEW stage)

POST /documents/{doc_id}/chunks
    Body: {text, element_type, page_number, ...}
    Creates new Chunk with approved=False

POST /documents/{doc_id}/approve
    Validates: all chunks have non-empty text
    Dispatches: index_document.delay(doc_id)
    Returns: {task_id, status: "indexing"}

POST /documents/{doc_id}/approve-all
    Skips review, marks all approved=True, dispatches index_document
    For trusted sources only
```

**`api/documents.py`**:

```
GET /documents?status=READY&page=1
    Paginated Document list with filters

GET /documents/{doc_id}
    Full Document + chunk_count

DELETE /documents/{doc_id}
    1. delete_by_doc_id(ES)
    2. Delete SQL chunks
    3. Delete file from storage
    4. Delete SQL document
    Returns: 204

PATCH /documents/{doc_id}
    Body: {tags, custom}
    Updates metadata, propagates tag change to ES via update_by_query

POST /documents/{doc_id}/reindex
    1. Delete existing chunks + vectors
    2. Reset status=PENDING
    3. Dispatch process_document
    Returns: {task_id}

GET /tasks/{task_id}
    Celery task state: {state, progress, error}

GET /metrics
    Returns: {
        documents_by_status: {...},
        total_chunks_indexed: int,
        avg_processing_time_ms: float,
        ocr_invocation_rate: float,
        queue_depth: int,
        language_distribution: {...}
    }
```

---

### 4.3 — Structured Logging

**Every pipeline stage logs exactly this shape**:

```python
logger.info("stage_complete",
    doc_id=doc_id,
    stage="parsing",  # intake|parsing|ocr|normalization|dedup|chunking|embedding|indexing
    duration_ms=elapsed,
    status="success",  # success|failed|skipped
    count=len(elements),
    parser="pdf_docling",  # which implementation
    extra={}  # stage-specific fields
)
```

**Why structured?** Enables querying: "Show all failed OCR tasks in last hour", "Average parse time per MIME type".

---

### 4.4 — Main App Retriever Update

**File**: `app/core/retriever.py`

**Changes**:
1. Remove `BM25Retriever` class
2. Remove `_bm25_search` method
3. Remove `_cpu_executor`
4. Add `USE_RRF` toggle:

```python
if settings.USE_RRF:
    # Use ElasticsearchStore with custom_query for RRF
    results = self.es_store.as_retriever().invoke(
        query,
        search_type="similarity",
        search_kwargs={
            "custom_query": build_rrf_query(query, embedding)
        }
    )
else:
    # Legacy: kNN only
    results = self.es_store.similarity_search(query, k=k)

# Reranker still applied if USE_RERANKER=True
if self.use_reranker:
    results = await self._rerank_async(query, results)
```

**Why this change?**
- BM25 is free in ES (built into `text` field)
- RRF combines kNN + BM25 in one query (less code, better fusion)
- Toggleable: can A/B test retrieval quality

---

### Phase 4 Tests

**File**: `tests/ingestion/test_pipeline.py`
- Full flow: upload → process → REVIEW → approve → READY
- Status transitions correct
- Crash recovery (kill worker mid-task)

**File**: `tests/ingestion/test_api.py`
- Upload endpoint: file accepted, task dispatched
- Review endpoint: chunk editing, approve
- Metrics endpoint: counts accurate

---

## Dependencies

```bash
# Core
pip install fastapi uvicorn celery aiofiles python-magic structlog

# Parsing
pip install python-docx python-pptx openpyxl trafilatura beautifulsoup4
pip install pymupdf pymupdf4llm docling tabulate

# Chunking
pip install chonkie langchain langchain-text-splitters llama-index

# Embedding
pip install FlagEmbedding

# Language detection
pip install lingua-language-detector

# Dedup
pip install datasketch

# ES
pip install elasticsearch

# System deps
# Ubuntu: sudo apt install libmagic1
# macOS:  brew install libmagic
```

**OCR engines** (not pip):
- dots.ocr: deploy via `vllm serve dots-community/dots-ocr-2.0`
- DeepDoc: clone from https://github.com/infiniflow/ragflow/tree/main/deepdoc
- Gemma: `ollama pull gemma2:27b`

---

## Build Order

```
Phase 1: Foundation & Intake
  ├─ models.py + database.py
  ├─ storage.py
  ├─ main.py + health endpoint
  ├─ intake.py
  ├─ Alembic migration
  └─ Tests: storage, models, intake

Phase 2: Parsing & Quality
  ├─ parsers/base.py + router.py
  ├─ parsers/docx.py, pdf_*.py, pptx.py, xlsx.py, html.py, text.py
  ├─ ocr/base.py + router.py
  ├─ ocr/dots.py, deepdoc.py, gemma.py
  ├─ normalizer.py
  ├─ dedup.py
  └─ Tests: parsers, ocr, normalizer, dedup

Phase 3: Chunking & Indexing
  ├─ chunkers/base.py + router.py
  ├─ chunkers/fixed.py, heading.py, semantic.py, sentence.py, hierarchical.py
  ├─ vector_store.py (embedding + ES)
  └─ Tests: chunkers, vector_store

Phase 4: Tasks & API
  ├─ tasks/worker.py + ingestion_task.py
  ├─ api/upload.py, review.py, documents.py
  ├─ pipeline.py (orchestrator tying all steps)
  ├─ app/core/retriever.py update (remove BM25, add RRF toggle)
  └─ Tests: pipeline, api, retrieval quality
```

---

This plan is complete. Every decision is explained. Every file is defined. Ready to hand off to a new chat with zero context loss.