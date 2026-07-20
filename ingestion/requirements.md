
poetry add "FlagEmbedding>=1.2.10" "lingua-language-detector>=2.0.2" "datasketch>=1.6.5" "Pillow>=10.3.0" "opencv-python-headless>=4.10.0" "ollama>=0.2.0" "python-docx>=1.1.0" "tabulate>=0.9.0" "docling>=2.5.0" "pymupdf>=1.24.0" "trafilatura>=1.10.0" "openpyxl>=3.1.2" "python-pptx>=0.6.23" "chonkie[all]>=0.4.0" "python-magic>=0.4.27" "python-multipart>=0.0.9" "aiofiles>=23.2.0" "aiomysql>=0.2.0" "llama-index-core>=0.10.0" "tree-sitter-language-pack==1.6.2"


----------------------------------
# requirements-ingestion.txt
# Usage: pip install -r requirements-ingestion.txt
# Or with poetry: poetry add $(grep -v '^#' requirements-ingestion.txt | tr '\n' ' ')

# ── Group 1: Always needed ────────────────────────────────────────────────────
aiomysql>=0.2.0
aiofiles>=23.2.0
python-multipart>=0.0.9
python-magic>=0.4.27

# ── Group 2: Parsing ──────────────────────────────────────────────────────────
python-docx>=1.1.0
tabulate>=0.9.0
python-pptx>=0.6.23
openpyxl>=3.1.2
trafilatura>=1.10.0
pymupdf>=1.24.0
# docling>=2.5.0          # OPTIONAL — heavy, comment out if not needed

# ── Group 3: Chunking ─────────────────────────────────────────────────────────
langchain-text-splitters>=0.2.0
chonkie[all]>=0.4.0
# llama-index-core>=0.10.0  # OPTIONAL — only for hierarchical chunker

# ── Group 4: Embedding ────────────────────────────────────────────────────────
# torch is already in your env — install separately with correct CUDA version
# GPU:  pip install torch --index-url https://download.pytorch.org/whl/cu121
# CPU:  pip install torch --index-url https://download.pytorch.org/whl/cpu
FlagEmbedding>=1.2.10

# ── Group 5: Language & quality ───────────────────────────────────────────────
lingua-language-detector>=2.0.2
datasketch>=1.6.5
Pillow>=10.3.0

# ── Group 6: OCR ──────────────────────────────────────────────────────────────
# opencv-python-headless>=4.10.0  # OPTIONAL — only for DeepDoc OCR
# ollama>=0.2.0                   # OPTIONAL — only for Gemma OCR

# ── Group 7: Storage & search ─────────────────────────────────────────────────
# miniopy-async>=1.20.0           # OPTIONAL — only for MinIO storage

# ── Group 8: Testing ──────────────────────────────────────────────────────────
pytest>=8.2.0
pytest-asyncio>=0.23.0
pytest-cov>=5.0.0
factory-boy>=3.3.0