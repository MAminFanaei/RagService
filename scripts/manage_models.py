# scripts/manage_models.py
"""
Local model management for the ingestion service.
Downloads and manages all ML models including OCR engines.

Usage:
    python scripts/manage_models.py download              # Download missing models
    python scripts/manage_models.py download --force      # Re-download all models
    python scripts/manage_models.py download --only ocr   # Only OCR models
    python scripts/manage_models.py list                  # List downloaded models
    python scripts/manage_models.py clean                 # Remove all models
    python scripts/manage_models.py verify                # Verify models work
"""

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
import json

# Project root
PROJECT_ROOT = Path(__file__).parent.parent
MODELS_DIR = PROJECT_ROOT / "models/injestion_models"

# Global flag for force re-download
FORCE_DOWNLOAD = False

# Model configurations
MODELS = {
    # ══════════════════════════════════════════════════════════════════════════
    # EMBEDDING MODELS
    # ══════════════════════════════════════════════════════════════════════════
    "embedding_bge_m3": {
        "name": "BAAI/bge-m3",
        "path": MODELS_DIR / "bge-m3",
        "size_gb": 2.2,
        "category": "embedding",
        "description": "BGE-M3 multilingual embedding model (dense + sparse)",
        "download_fn": "download_bge_m3",
        "verify_fn": "verify_bge_m3",
        "check_files": ["config.json", "pytorch_model.bin"],
    },
    "sentence_transformer": {
        "name": "sentence-transformers/all-MiniLM-L6-v2",
        "path": MODELS_DIR / "all-MiniLM-L6-v2",
        "size_gb": 0.09,
        "category": "embedding",
        "description": "Sentence transformer for semantic chunking",
        "download_fn": "download_sentence_transformer",
        "verify_fn": "verify_sentence_transformer",
        "check_files": ["config.json", "pytorch_model.bin"],
    },
    
    # ══════════════════════════════════════════════════════════════════════════
    # OCR MODELS - DeepDoc
    # ══════════════════════════════════════════════════════════════════════════
    "deepdoc_layout": {
        "name": "deepdoc-layout-detection",
        "path": MODELS_DIR / "deepdoc" / "layout",
        "size_gb": 0.3,
        "category": "ocr",
        "description": "DeepDoc layout detection (YOLO-based)",
        "download_fn": "download_deepdoc_layout",
        "verify_fn": "verify_deepdoc",
        "check_files": ["layout_yolo.pt"],
    },
    "deepdoc_ocr": {
        "name": "deepdoc-text-recognition",
        "path": MODELS_DIR / "deepdoc" / "ocr",
        "size_gb": 0.15,
        "category": "ocr",
        "description": "DeepDoc OCR text recognition (PaddleOCR)",
        "download_fn": "download_deepdoc_ocr",
        "verify_fn": "verify_deepdoc",
        "check_files": ["det", "rec", "cls"],  # directories
    },
    "deepdoc_table": {
        "name": "deepdoc-table-structure",
        "path": MODELS_DIR / "deepdoc" / "table",
        "size_gb": 0.2,
        "category": "ocr",
        "description": "DeepDoc table structure recognition",
        "download_fn": "download_deepdoc_table",
        "verify_fn": "verify_deepdoc",
        "check_files": ["table_rec.pt"],
    },
    
    # ══════════════════════════════════════════════════════════════════════════
    # OCR MODELS - dots.ocr (vLLM served)
    # ══════════════════════════════════════════════════════════════════════════
    "dots_ocr": {
        "name": "dots-community/dots-ocr-2.0",
        "path": MODELS_DIR / "dots-ocr-2.0",
        "size_gb": 2.3,
        "category": "ocr",
        "description": "dots.ocr 2.0 model for vLLM (best OCR quality)",
        "download_fn": "download_dots_ocr",
        "verify_fn": "verify_dots_ocr",
        "check_files": ["config.json", "pytorch_model.bin.index.json"],
    },
    
    # ══════════════════════════════════════════════════════════════════════════
    # OCR MODELS - RapidOCR (lightweight alternative)
    # ══════════════════════════════════════════════════════════════════════════
    "rapidocr_det": {
        "name": "rapidocr-det-model",
        "path": MODELS_DIR / "rapidocr",
        "size_gb": 0.003,
        "category": "ocr",
        "description": "RapidOCR text detection",
        "download_fn": "download_rapidocr",
        "verify_fn": "verify_rapidocr",
        "check_files": ["ch_PP-OCRv4_det_mobile.onnx"],
    },
    "rapidocr_rec": {
        "name": "rapidocr-rec-model",
        "path": MODELS_DIR / "rapidocr",
        "size_gb": 0.008,
        "category": "ocr",
        "description": "RapidOCR text recognition",
        "download_fn": "download_rapidocr",
        "verify_fn": "verify_rapidocr",
        "check_files": ["ch_PP-OCRv4_rec_mobile.onnx"],
    },
    "rapidocr_cls": {
        "name": "rapidocr-cls-model",
        "path": MODELS_DIR / "rapidocr",
        "size_gb": 0.002,
        "category": "ocr",
        "description": "RapidOCR text angle classification",
        "download_fn": "download_rapidocr",
        "verify_fn": "verify_rapidocr",
        "check_files": ["ch_ppocr_mobile_v2.0_cls_mobile.onnx"],
    },
    
    # ══════════════════════════════════════════════════════════════════════════
    # PARSING MODELS
    # ══════════════════════════════════════════════════════════════════════════
    "docling": {
        "name": "docling-models",
        "path": MODELS_DIR / "docling",
        "size_gb": 1.5,
        "category": "parsing",
        "description": "Docling PDF layout analysis models",
        "download_fn": "download_docling_models",
        "verify_fn": "verify_docling",
        "check_files": ["models"],  # directory
    },
    
    # ══════════════════════════════════════════════════════════════════════════
    # LANGUAGE DETECTION
    # ══════════════════════════════════════════════════════════════════════════
    "lingua": {
        "name": "lingua-language-models",
        "path": MODELS_DIR / "lingua",
        "size_gb": 0.05,
        "category": "language",
        "description": "Lingua language detection models",
        "download_fn": "download_lingua_models",
        "verify_fn": "verify_lingua",
        "check_files": [],  # Lingua stores in package directory
    },
}


# ══════════════════════════════════════════════════════════════════════════════
# HELPER FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def check_model_exists(model_info: dict) -> bool:
    """
    Check if model is already downloaded by verifying essential files exist.
    
    Args:
        model_info: Model configuration dict
        
    Returns:
        True if all check files exist, False otherwise
    """
    path = model_info["path"]
    check_files = model_info.get("check_files", [])
    
    if not path.exists():
        return False
    
    if not check_files:
        # No specific files to check, just verify directory exists and is not empty
        return path.exists() and any(path.iterdir())
    
    # Check all required files exist
    for check_file in check_files:
        file_path = path / check_file
        if not file_path.exists():
            return False
    
    return True


def should_download(model_key: str) -> bool:
    """
    Determine if a model should be downloaded.
    
    Returns True if:
    - Force flag is set, OR
    - Model doesn't exist
    """
    global FORCE_DOWNLOAD
    
    model_info = MODELS[model_key]
    
    if FORCE_DOWNLOAD:
        return True
    
    if check_model_exists(model_info):
        print(f"  ⏭️  Already exists: {model_info['path']}")
        print(f"     Use --force to re-download")
        return False
    
    return True


# ══════════════════════════════════════════════════════════════════════════════
# DOWNLOAD FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def download_bge_m3():
    """Download BGE-M3 embedding model."""
    if not should_download("embedding_bge_m3"):
        return True
    
    print("\n▶ Downloading BGE-M3 embedding model (~2.2GB)...")
    print("  This may take several minutes...")
    
    try:
        from huggingface_hub import snapshot_download
        
        model_path = snapshot_download(
            repo_id="BAAI/bge-m3",
            cache_dir=MODELS_DIR / "bge-m3",
            local_dir=MODELS_DIR / "bge-m3",
            local_dir_use_symlinks=False,
        )
        
        print(f"  ✅ Downloaded to: {model_path}")
        return True
        
    except ImportError:
        print("  ❌ Install first: pip install huggingface-hub")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def download_sentence_transformer():
    """Download sentence transformer for semantic chunking."""
    if not should_download("sentence_transformer"):
        return True
    
    print("\n▶ Downloading sentence transformer (~90MB)...")
    
    try:
        from huggingface_hub import snapshot_download
        
        model_path = snapshot_download(
            repo_id="sentence-transformers/all-MiniLM-L6-v2",
            cache_dir=MODELS_DIR / "all-MiniLM-L6-v2",
            local_dir=MODELS_DIR / "all-MiniLM-L6-v2",
            local_dir_use_symlinks=False,
        )
        
        print(f"  ✅ Downloaded to: {model_path}")
        return True
        
    except ImportError:
        print("  ❌ Install first: pip install huggingface-hub")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def download_deepdoc_layout():
    """Download DeepDoc layout detection model."""
    if not should_download("deepdoc_layout"):
        return True
    
    print("\n▶ Downloading DeepDoc layout detection model (~300MB)...")
    print("  Cloning RAGFlow repository (contains all DeepDoc models)...")
    
    try:
        # Clone RAGFlow repo with sparse checkout (only deepdoc directory)
        ragflow_dir = PROJECT_ROOT / "external" / "ragflow"
        ragflow_deepdoc = ragflow_dir / "deepdoc"
        
        if not ragflow_deepdoc.exists():
            print(f"  📦 Cloning RAGFlow/deepdoc...")
            
            # Create external directory
            (PROJECT_ROOT / "external").mkdir(exist_ok=True)
            
            # Clone with sparse checkout
            subprocess.run([
                "git", "clone", "--depth", "1", "--filter=blob:none", "--sparse",
                "https://github.com/infiniflow/ragflow.git",
                str(ragflow_dir)
            ], check=True, capture_output=True)
            
            subprocess.run([
                "git", "-C", str(ragflow_dir),
                "sparse-checkout", "set", "deepdoc"
            ], check=True, capture_output=True)
            
            print(f"  ✅ Cloned RAGFlow repository")
        
        # Copy deepdoc models to our models directory
        if ragflow_deepdoc.exists():
            deepdoc_models_src = ragflow_deepdoc / "models"
            deepdoc_models_dst = MODELS_DIR / "deepdoc"
            
            if deepdoc_models_src.exists():
                deepdoc_models_dst.mkdir(parents=True, exist_ok=True)
                
                # Copy all model subdirectories
                for item in deepdoc_models_src.iterdir():
                    target = deepdoc_models_dst / item.name
                    if item.is_dir():
                        shutil.copytree(item, target, dirs_exist_ok=True)
                        print(f"  ✅ Copied: {item.name}")
                    else:
                        shutil.copy2(item, target)
                        print(f"  ✅ Copied: {item.name}")
                
                print(f"  ✅ All DeepDoc models copied to: {deepdoc_models_dst}")
            else:
                print(f"  ⚠️  Models directory not found in RAGFlow clone")
                return False
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"  ❌ Git clone failed: {e}")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def download_deepdoc_ocr():
    """Download DeepDoc OCR model (PaddleOCR)."""
    if not should_download("deepdoc_ocr"):
        return True
    
    print("\n▶ Downloading DeepDoc OCR model (~150MB)...")
    print("  This will be downloaded with deepdoc_layout...")
    
    # DeepDoc OCR models are included in the RAGFlow clone
    # Just verify they exist or call download_deepdoc_layout
    return download_deepdoc_layout()


def download_deepdoc_table():
    """Download DeepDoc table structure recognition model."""
    if not should_download("deepdoc_table"):
        return True
    
    print("\n▶ Downloading DeepDoc table model (~200MB)...")
    print("  This will be downloaded with deepdoc_layout...")
    
    # Table models are included in the RAGFlow clone
    return download_deepdoc_layout()


def download_dots_ocr():
    """Download dots.ocr model for vLLM."""
    if not should_download("dots_ocr"):
        return True
    
    print("\n▶ Downloading dots.ocr 2.0 model (~2.3GB)...")
    print("  This model will be served via vLLM for best OCR quality")
    
    try:
        from huggingface_hub import snapshot_download
        
        model_path = snapshot_download(
            repo_id="dots-community/dots-ocr-2.0",
            cache_dir=MODELS_DIR / "dots-ocr-2.0",
            local_dir=MODELS_DIR / "dots-ocr-2.0",
            local_dir_use_symlinks=False,
        )
        
        print(f"  ✅ Downloaded to: {model_path}")
        print(f"  ℹ️  To serve: vllm serve {model_path} --port 8000")
        return True
        
    except ImportError:
        print("  ❌ Install first: pip install huggingface-hub vllm")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def download_rapidocr():
    """Download RapidOCR models (lightweight ONNX)."""
    # Check if any RapidOCR model needs downloading
    needs_download = False
    for key in ["rapidocr_det", "rapidocr_rec", "rapidocr_cls"]:
        if should_download(key):
            needs_download = True
            break
    
    if not needs_download:
        return True
    
    print("\n▶ Downloading RapidOCR models (~13MB total)...")
    
    try:
        from rapidocr_onnxruntime import RapidOCR
        
        # Models auto-download on first use
        # Create an instance to trigger download
        ocr = RapidOCR()
        
        # RapidOCR stores models in package directory by default
        # Copy them to our models dir
        import rapidocr_onnxruntime
        package_dir = Path(rapidocr_onnxruntime.__file__).parent
        models_src = package_dir / "models"
        
        if models_src.exists():
            target = MODELS_DIR / "rapidocr"
            target.mkdir(exist_ok=True, parents=True)
            
            for model_file in models_src.glob("*.onnx"):
                shutil.copy2(model_file, target / model_file.name)
                print(f"  ✅ Copied: {model_file.name}")
            
            return True
        else:
            print(f"  ⚠️  RapidOCR models not found in package directory")
            return False
        
    except ImportError:
        print("  ⏭️  RapidOCR not installed - skipping")
        print("     Install: pip install rapidocr-onnxruntime")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def download_docling_models():
    """Download Docling models."""
    if not should_download("docling"):
        return True
    
    print("\n▶ Downloading Docling models (~1.5GB)...")
    
    try:
        from docling.document_converter import DocumentConverter
        
        # Docling auto-downloads models on first use
        print("  Creating converter (triggers model download)...")
        converter = DocumentConverter()
        
        # Copy from default cache to our models dir
        docling_cache = Path.home() / ".cache" / "docling"
        if docling_cache.exists():
            target = MODELS_DIR / "docling"
            target.mkdir(exist_ok=True, parents=True)
            
            for item in docling_cache.rglob("*"):
                if item.is_file():
                    rel_path = item.relative_to(docling_cache)
                    target_file = target / rel_path
                    target_file.parent.mkdir(exist_ok=True, parents=True)
                    shutil.copy2(item, target_file)
            
            print(f"  ✅ Copied models to: {target}")
            return True
        else:
            print(f"  ⚠️  Docling cache not found")
            return False
        
    except ImportError:
        print("  ⏭️  Docling not installed - skipping")
        print("     Install: pip install docling")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


def download_lingua_models():
    """Download Lingua language detection models."""
    if not should_download("lingua"):
        return True
    
    print("\n▶ Downloading Lingua models (~50MB)...")
    
    try:
        from lingua import LanguageDetectorBuilder, Language
        
        # Build detector (triggers download)
        detector = LanguageDetectorBuilder.from_languages(
            Language.ENGLISH,
            Language.PERSIAN,
            Language.ARABIC,
            Language.FRENCH,
            Language.GERMAN,
            Language.SPANISH,
        ).build()
        
        print(f"  ✅ Lingua models ready")
        print(f"  ℹ️  Models stored in package directory (lingua manages them)")
        return True
        
    except ImportError:
        print("  ⏭️  Lingua not installed - skipping")
        print("     Install: pip install lingua-language-detector")
        return False
    except Exception as e:
        print(f"  ❌ Failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# VERIFY FUNCTIONS
# ══════════════════════════════════════════════════════════════════════════════

def verify_bge_m3():
    """Verify BGE-M3 model."""
    try:
        from FlagEmbedding import BGEM3FlagModel
        model = BGEM3FlagModel(str(MODELS_DIR / "bge-m3"), use_fp16=False, device="cpu")
        result = model.encode(["test"], return_dense=True)
        print(f"  ✅ BGE-M3 OK - dim: {result['dense_vecs'].shape[1]}")
        return True
    except Exception as e:
        print(f"  ❌ BGE-M3 failed: {e}")
        return False


def verify_sentence_transformer():
    """Verify sentence transformer."""
    try:
        from sentence_transformers import SentenceTransformer
        model = SentenceTransformer(str(MODELS_DIR / "all-MiniLM-L6-v2"))
        embeddings = model.encode(["test"])
        print(f"  ✅ Sentence Transformer OK - dim: {embeddings.shape[1]}")
        return True
    except Exception as e:
        print(f"  ❌ Sentence Transformer failed: {e}")
        return False


def verify_deepdoc():
    """Verify DeepDoc models."""
    try:
        # Check if model files exist
        layout_model = MODELS_DIR / "deepdoc" / "layout"
        ocr_model = MODELS_DIR / "deepdoc" / "ocr"
        table_model = MODELS_DIR / "deepdoc" / "table"
        
        checks = []
        if layout_model.exists():
            checks.append("layout ✅")
        else:
            checks.append("layout ❌")
        
        if ocr_model.exists():
            checks.append("ocr ✅")
        else:
            checks.append("ocr ❌")
        
        if table_model.exists():
            checks.append("table ✅")
        else:
            checks.append("table ❌")
        
        print(f"  DeepDoc models: {', '.join(checks)}")
        
        all_exist = layout_model.exists() and ocr_model.exists() and table_model.exists()
        return all_exist
        
    except Exception as e:
        print(f"  ❌ DeepDoc check failed: {e}")
        return False


def verify_dots_ocr():
    """Verify dots.ocr model."""
    try:
        model_path = MODELS_DIR / "dots-ocr-2.0"
        config_file = model_path / "config.json"
        
        if config_file.exists():
            print(f"  ✅ dots.ocr model present")
            print(f"  ℹ️  Start server: vllm serve {model_path} --port 8000")
            return True
        else:
            print(f"  ❌ dots.ocr model not found")
            return False
    except Exception as e:
        print(f"  ❌ dots.ocr check failed: {e}")
        return False


def verify_rapidocr():
    """Verify RapidOCR models."""
    try:
        rapidocr_dir = MODELS_DIR / "rapidocr"
        
        required_files = [
            "ch_PP-OCRv4_det_mobile.onnx",
            "ch_PP-OCRv4_rec_mobile.onnx",
            "ch_ppocr_mobile_v2.0_cls_mobile.onnx",
        ]
        
        missing = []
        for filename in required_files:
            if not (rapidocr_dir / filename).exists():
                missing.append(filename)
        
        if not missing:
            print(f"  ✅ RapidOCR models complete")
            return True
        else:
            print(f"  ❌ RapidOCR missing: {', '.join(missing)}")
            return False
            
    except Exception as e:
        print(f"  ❌ RapidOCR check failed: {e}")
        return False


def verify_docling():
    """Verify Docling models."""
    try:
        docling_models = MODELS_DIR / "docling"
        if docling_models.exists() and any(docling_models.iterdir()):
            print(f"  ✅ Docling models present")
            return True
        else:
            print(f"  ❌ Docling models not found")
            return False
    except Exception as e:
        print(f"  ❌ Docling check failed: {e}")
        return False


def verify_lingua():
    """Verify Lingua models."""
    try:
        from lingua import LanguageDetectorBuilder, Language
        detector = LanguageDetectorBuilder.from_languages(
            Language.ENGLISH, Language.PERSIAN, Language.ARABIC
        ).build()
        result = detector.detect_language_of("Hello world")
        print(f"  ✅ Lingua OK - detected: {result.name if result else 'None'}")
        return True
    except Exception as e:
        print(f"  ❌ Lingua failed: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
# MAIN COMMANDS
# ══════════════════════════════════════════════════════════════════════════════

def setup_directories():
    """Create models directory structure."""
    MODELS_DIR.mkdir(exist_ok=True)
    (MODELS_DIR / "deepdoc").mkdir(exist_ok=True)
    (MODELS_DIR / "rapidocr").mkdir(exist_ok=True)
    print(f"✅ Models directory: {MODELS_DIR}")


def download_all(category_filter=None, force=False):
    """Download all models or specific category."""
    global FORCE_DOWNLOAD
    FORCE_DOWNLOAD = force
    
    setup_directories()
    
    # Filter models by category if specified
    models_to_download = MODELS
    if category_filter:
        models_to_download = {
            k: v for k, v in MODELS.items()
            if v["category"] == category_filter
        }
    
    # Calculate total size (only count unique models to avoid double-counting rapidocr)
    seen_paths = set()
    total_size = 0
    for v in models_to_download.values():
        path_str = str(v["path"])
        if path_str not in seen_paths:
            seen_paths.add(path_str)
            total_size += v["size_gb"]
    
    print(f"\n{'═'*70}")
    print(f"  MODEL DOWNLOAD")
    if category_filter:
        print(f"  Category: {category_filter}")
    if force:
        print(f"  Mode: FORCE RE-DOWNLOAD")
    else:
        print(f"  Mode: Skip existing models")
    print(f"{'═'*70}")
    print(f"  Target directory: {MODELS_DIR}")
    print(f"  Total size: ~{total_size:.1f} GB")
    print(f"  Models to check: {len(models_to_download)}")
    print(f"{'═'*70}\n")
    
    # Group by download function to avoid downloading same thing multiple times
    download_fns_called = set()
    results = {}
    
    for key, info in models_to_download.items():
        download_fn_name = info["download_fn"]
        
        # Skip if we already called this download function
        if download_fn_name in download_fns_called:
            continue
        
        download_fns_called.add(download_fn_name)
        download_fn = globals().get(download_fn_name)
        
        if download_fn:
            results[info["name"]] = download_fn()
        else:
            print(f"⚠️  No download function for {info['name']}")
            results[info["name"]] = False
    
    print(f"\n{'═'*70}")
    print(f"  SUMMARY")
    print(f"{'═'*70}")
    
    success_count = sum(1 for v in results.values() if v)
    print(f"  Downloaded: {success_count}/{len(results)}")
    print()
    
    for name, success in results.items():
        status = "✅" if success else "❌"
        print(f"  {status}  {name}")
    
    print(f"\n📁 Models location: {MODELS_DIR}")
    print(f"💾 Disk usage: {get_dir_size(MODELS_DIR):.2f} GB\n")


def list_models():
    """List downloaded models."""
    print(f"\n{'═'*70}")
    print(f"  INSTALLED MODELS")
    print(f"{'═'*70}\n")
    
    # Group by category
    by_category = {}
    for key, info in MODELS.items():
        cat = info["category"]
        if cat not in by_category:
            by_category[cat] = []
        by_category[cat].append((key, info))
    
    for category, models in sorted(by_category.items()):
        print(f"📦 {category.upper()}")
        print("─" * 70)
        
        # Track unique paths to avoid listing duplicates
        seen_paths = set()
        
        for key, info in models:
            path = info["path"]
            path_str = str(path)
            
            # Skip if we already showed this path
            if path_str in seen_paths:
                continue
            seen_paths.add(path_str)
            
            exists = check_model_exists(info)
            status = "✅" if exists else "❌"
            size = get_dir_size(path) if exists else 0
            
            print(f"{status}  {info['name']}")
            print(f"     {info['description']}")
            print(f"     Path: {path}")
            if exists:
                print(f"     Size: {size:.2f} GB")
            else:
                print(f"     Status: Not downloaded")
            print()
    
    total_size = get_dir_size(MODELS_DIR)
    print(f"{'═'*70}")
    print(f"📁 Total disk usage: {total_size:.2f} GB\n")


def clean_models():
    """Remove all downloaded models."""
    if not MODELS_DIR.exists():
        print("No models directory found.")
        return
    
    size = get_dir_size(MODELS_DIR)
    
    print(f"\n{'═'*70}")
    print(f"  WARNING: DELETE ALL MODELS")
    print(f"{'═'*70}")
    print(f"  Directory: {MODELS_DIR}")
    print(f"  Size: {size:.2f} GB")
    print(f"{'═'*70}\n")
    
    response = input(f"⚠️  Delete all models? Type 'yes' to confirm: ")
    if response.lower() != 'yes':
        print("Cancelled.")
        return
    
    shutil.rmtree(MODELS_DIR)
    print(f"✅ Deleted {MODELS_DIR}")
    
    # Also clean external/ragflow if it exists
    ragflow_dir = PROJECT_ROOT / "external" / "ragflow"
    if ragflow_dir.exists():
        response = input(f"⚠️  Also delete RAGFlow clone at {ragflow_dir}? [y/N]: ")
        if response.lower() == 'y':
            shutil.rmtree(ragflow_dir)
            print(f"✅ Deleted {ragflow_dir}")


def verify_all():
    """Verify all models can be loaded."""
    print(f"\n{'═'*70}")
    print(f"  MODEL VERIFICATION")
    print(f"{'═'*70}\n")
    
    # Group by verify function to avoid duplicate checks
    verify_fns_called = set()
    
    for key, info in MODELS.items():
        verify_fn_name = info["verify_fn"]
        
        # Skip if already called
        if verify_fn_name in verify_fns_called:
            continue
        
        verify_fns_called.add(verify_fn_name)
        
        print(f"▶ Testing {info['name']}...")
        verify_fn = globals().get(verify_fn_name)
        if verify_fn:
            verify_fn()
        else:
            print(f"  ⚠️  No verify function")
        print()


def get_dir_size(path: Path) -> float:
    """Get directory size in GB."""
    if not path.exists():
        return 0
    
    total = 0
    for item in path.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except (PermissionError, FileNotFoundError):
                pass
    
    return total / (1024 ** 3)  # Convert to GB


def main():
    parser = argparse.ArgumentParser(
        description="Manage ML models for ingestion service",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Download only missing models
  python manage_models.py download
  
  # Force re-download all models
  python manage_models.py download --force
  
  # Download only OCR models
  python manage_models.py download --only ocr
  
  # Force re-download only embedding models
  python manage_models.py download --only embedding --force
  
  # List what's installed
  python manage_models.py list
  
  # Verify models work
  python manage_models.py verify
  
  # Clean up all models
  python manage_models.py clean
        """
    )
    
    parser.add_argument(
        "command",
        choices=["download", "list", "clean", "verify"],
        help="Command to run",
    )
    parser.add_argument(
        "--only",
        choices=["embedding", "ocr", "parsing", "language"],
        help="Only download models from specific category",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force re-download even if models already exist",
    )
    
    args = parser.parse_args()
    
    if args.command == "download":
        download_all(category_filter=args.only, force=args.force)
    elif args.command == "list":
        list_models()
    elif args.command == "clean":
        clean_models()
    elif args.command == "verify":
        verify_all()


if __name__ == "__main__":
    main()