# test_quality_pdf.py

import json
import time
from pathlib import Path
from typing import Any

def safe_str(obj: Any, max_len: int = 1000) -> str:
    """Convert any object to string, truncate if needed."""
    s = str(obj)
    return s if len(s) <= max_len else s[:max_len] + "...[truncated]"

def save_result(output_path: Path, method: str, data: dict):
    """Save method output to JSON."""
    with open(output_path / f"{method}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {method:<20} → {output_path / f'{method}.json'}")


def run_pdf_tests(file_path: Path, output_dir: Path, args):
    print(f"📄 Testing PDF parsers on: {file_path.name}\n")
    
    with open(file_path, "rb") as f:
        pdf_bytes = f.read()
    
    parsers_to_test = args.parsers
    if "all" in parsers_to_test:
        parsers_to_test = ["pymupdf", "pymupdf4llm", "docling"]
    
    # ── PyMuPDF ───────────────────────────────────────────────────────────────
    if "pymupdf" in parsers_to_test:
        print("▶ Testing PyMuPDF...")
        try:
            import fitz
            t0 = time.perf_counter()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            pages = []
            for page_num, page in enumerate(doc, start=1):
                text = page.get_text("text")
                blocks = page.get_text("blocks")
                images = page.get_images()
                
                # Get actual text blocks with positions
                structured_blocks = []
                for block in blocks:
                    if block[6] == 0:  # text block
                        structured_blocks.append({
                            "bbox": block[:4],
                            "text": block[4],
                            "block_no": block[5],
                        })
                
                pages.append({
                    "page_number": page_num,
                    "raw_text": text[:2000],  # preview
                    "raw_text_length": len(text),
                    "image_count": len(images),
                    "block_count": len(structured_blocks),
                    "blocks_preview": structured_blocks[:10],
                    "is_scanned": len(text.strip()) < 50 and len(images) > 0,
                })
            
            elapsed = time.perf_counter() - t0
            
            result = {
                "parser": "pymupdf",
                "file": file_path.name,
                "page_count": len(pages),
                "time_seconds": round(elapsed, 3),
                "pages": pages,
                "verdict": {
                    "has_text": any(p["raw_text_length"] > 0 for p in pages),
                    "has_images": any(p["image_count"] > 0 for p in pages),
                    "scanned_pages": [p["page_number"] for p in pages if p["is_scanned"]],
                    "quality": "✅ Text extracted" if any(p["raw_text_length"] > 100 for p in pages) else "⚠️ Scanned/no text",
                }
            }
            save_result(output_dir, "parser_pymupdf", result)
            
        except Exception as e:
            print(f"  ❌ PyMuPDF failed: {e}")
    
    # ── PyMuPDF (Simple mode) ─────────────────────────────────────────────────
    if "pymupdf" in parsers_to_test:
        print("▶ Testing PyMuPDF (simple text extraction)...")
        try:
            import fitz
            
            t0 = time.perf_counter()
            doc = fitz.open(stream=pdf_bytes, filetype="pdf")
            
            # Simple concatenation of all text
            full_text = ""
            for page in doc:
                full_text += page.get_text("text") + "\n\n"
            
            elapsed = time.perf_counter() - t0
            
            result = {
                "parser": "pymupdf_simple",
                "file": file_path.name,
                "page_count": doc.page_count,
                "time_seconds": round(elapsed, 3),
                "text_preview": full_text[:3000],
                "full_length": len(full_text),
                "verdict": {
                    "quality": "✅ Text extracted" if len(full_text) > 100 else "⚠️ No text found"
                }
            }
            save_result(output_dir, "parser_pymupdf_simple", result)
            
        except Exception as e:
            print(f"  ❌ PyMuPDF failed: {e}")
    
    # ── Docling ───────────────────────────────────────────────────────────────
    if "docling" in parsers_to_test:
        print("▶ Testing Docling (may download model on first run)...")
        try:
            import tempfile
            import os
            from docling.document_converter import DocumentConverter
            
            # Docling needs a file path
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp.write(pdf_bytes)
                tmp_path = tmp.name
            
            try:
                t0 = time.perf_counter()
                converter = DocumentConverter()
                result_doc = converter.convert(tmp_path)
                doc = result_doc.document
                elapsed = time.perf_counter() - t0
                
                # Extract all elements
                elements = []
                for item, level in doc.iterate_items():
                    elements.append({
                        "type": type(item).__name__,
                        "level": level,
                        "text": safe_str(item, max_len=500),
                    })
                
                # Group by type
                type_counts = {}
                for e in elements:
                    t = e["type"]
                    type_counts[t] = type_counts.get(t, 0) + 1
                
                result = {
                    "parser": "docling",
                    "file": file_path.name,
                    "time_seconds": round(elapsed, 3),
                    "element_count": len(elements),
                    "elements_preview": elements[:30],
                    "element_types": type_counts,
                    "verdict": {
                        "has_tables": "TableItem" in type_counts,
                        "has_headings": "SectionHeaderItem" in type_counts,
                        "has_figures": "FigureItem" in type_counts,
                        "quality": "✅ Rich structure" if len(type_counts) > 2 else "⚠️ Limited structure"
                    }
                }
                save_result(output_dir, "parser_docling", result)
                
            finally:
                os.unlink(tmp_path)
                
        except ImportError:
            print(f"  ⏭️  Docling not installed — skip")
        except Exception as e:
            print(f"  ❌ Docling failed: {e}")
    
    # ── Now test chunkers on the BEST parser output ──────────────────────────
    print(f"\n📦 Testing chunkers...\n")
    
    # Use PyMuPDF4LLM markdown as input (most parsers can't be easily chained)
    # In real code, you'd use normalized ParsedElements
    try:
        import fitz
        import pymupdf4llm
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        source_text = pymupdf4llm.to_markdown(doc)
    except:
        print("⚠️  Can't get source text for chunking — using PyMuPDF raw")
        import fitz
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
        source_text = "\n\n".join(page.get_text("text") for page in doc)
    
    if not source_text.strip():
        print("⚠️  No text extracted — skipping chunker tests")
        return
    
    chunkers_to_test = args.chunkers
    if "all" in chunkers_to_test:
        chunkers_to_test = ["fixed", "sentence", "semantic", "token"]
    
    # ── Fixed chunker ─────────────────────────────────────────────────────────
    if "fixed" in chunkers_to_test:
        print("▶ Testing Fixed chunker...")
        try:
            from langchain_text_splitters import RecursiveCharacterTextSplitter
            
            t0 = time.perf_counter()
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=500,
                chunk_overlap=50,
                separators=["\n\n", "\n", ".", " ", ""],
            )
            chunks = splitter.split_text(source_text)
            elapsed = time.perf_counter() - t0
            
            result = {
                "chunker": "fixed",
                "chunk_count": len(chunks),
                "time_seconds": round(elapsed, 3),
                "chunks_preview": [
                    {"index": i, "text": c[:500], "length": len(c)}
                    for i, c in enumerate(chunks[:args.max_chunks])
                ],
                "stats": {
                    "min_length": min(len(c) for c in chunks),
                    "max_length": max(len(c) for c in chunks),
                    "avg_length": sum(len(c) for c in chunks) // len(chunks),
                },
                "verdict": {
                    "quality": "✅ Consistent sizes" if all(400 <= len(c) <= 600 for c in chunks) else "⚠️ Size variance"
                }
            }
            save_result(output_dir, "chunker_fixed", result)
            
        except Exception as e:
            print(f"  ❌ Fixed chunker failed: {e}")
    
    # ── Sentence chunker ──────────────────────────────────────────────────────
    if "sentence" in chunkers_to_test:
        print("▶ Testing Sentence chunker...")
        try:
            from chonkie import SentenceChunker
            
            t0 = time.perf_counter()
            chunker = SentenceChunker(chunk_size=200, chunk_overlap=20)
            chunks = chunker.chunk(source_text)
            elapsed = time.perf_counter() - t0
            
            result = {
                "chunker": "sentence",
                "chunk_count": len(chunks),
                "time_seconds": round(elapsed, 3),
                "chunks_preview": [
                    {"index": i, "text": c.text[:500], "length": len(c.text)}
                    for i, c in enumerate(chunks[:args.max_chunks])
                ],
                "verdict": {
                    "quality": "✅ Sentence boundaries respected"
                }
            }
            save_result(output_dir, "chunker_sentence", result)
            
        except ImportError:
            print(f"  ⏭️  Chonkie not installed — skip")
        except Exception as e:
            print(f"  ❌ Sentence chunker failed: {e}")
    
    # ── Semantic chunker ──────────────────────────────────────────────────────
    if "semantic" in chunkers_to_test:
        print("▶ Testing Semantic chunker (downloads model on first run)...")
        try:
            from chonkie import SemanticChunker
            
            t0 = time.perf_counter()
            chunker = SemanticChunker(
                embedding_model="./models/gte-multilingual-base",
                chunk_size=200,
                threshold=0.5,
                
            )
            chunks = chunker.chunk(source_text)
            elapsed = time.perf_counter() - t0
            
            result = {
                "chunker": "semantic",
                "chunk_count": len(chunks),
                "time_seconds": round(elapsed, 3),
                "chunks_preview": [
                    {"index": i, "text": c.text[:500], "length": len(c.text)}
                    for i, c in enumerate(chunks[:args.max_chunks])
                ],
                "verdict": {
                    "quality": "✅ Topic-aware splits" if elapsed < 10 else "⚠️ Slow (but quality may be better)"
                }
            }
            save_result(output_dir, "chunker_semantic", result)
            
        except ImportError:
            print(f"  ⏭️  Chonkie not installed — skip")
        except Exception as e:
            print(f"  ❌ Semantic chunker failed: {e}")
    
    # ── Token chunker ─────────────────────────────────────────────────────────
    if "token" in chunkers_to_test:
        print("▶ Testing Token chunker...")
        try:
            from chonkie import TokenChunker
            
            t0 = time.perf_counter()
            chunker = TokenChunker(
                tokenizer="gpt2",
                chunk_size=200,
                chunk_overlap=20,
            )
            chunks = chunker.chunk(source_text)
            elapsed = time.perf_counter() - t0
            
            result = {
                "chunker": "token",
                "chunk_count": len(chunks),
                "time_seconds": round(elapsed, 3),
                "chunks_preview": [
                    {"index": i, "text": c.text[:500], "length": len(c.text)}
                    for i, c in enumerate(chunks[:args.max_chunks])
                ],
                "verdict": {
                    "quality": "✅ Exact token control"
                }
            }
            save_result(output_dir, "chunker_token", result)
            
        except ImportError:
            print(f"  ⏭️  Chonkie not installed — skip")
        except Exception as e:
            print(f"  ❌ Token chunker failed: {e}")