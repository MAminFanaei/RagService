# test_quality_text.py

import json
import time
from pathlib import Path

def save_result(output_path, method, data):
    with open(output_path / f"{method}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {method:<20} → {output_path / f'{method}.json'}")

def run_text_tests(file_path: Path, output_dir: Path, args):
    print(f"📝 Testing text parser on: {file_path.name}\n")
    
    with open(file_path, "r", encoding="utf-8") as f:
        content = f.read()
    
    is_markdown = file_path.suffix.lower() == ".md"
    
    if is_markdown:
        print("▶ Testing Markdown splitter...")
        try:
            from langchain_text_splitters import MarkdownHeaderTextSplitter
            
            t0 = time.perf_counter()
            headers_to_split_on = [
                ("#", "Header 1"),
                ("##", "Header 2"),
                ("###", "Header 3"),
            ]
            splitter = MarkdownHeaderTextSplitter(headers_to_split_on)
            chunks = splitter.split_text(content)
            elapsed = time.perf_counter() - t0
            
            result = {
                "parser": "markdown_header_splitter",
                "file": file_path.name,
                "time_seconds": round(elapsed, 3),
                "chunk_count": len(chunks),
                "chunks_preview": [
                    {"index": i, "metadata": c.metadata, "text": c.page_content[:500]}
                    for i, c in enumerate(chunks[:args.max_chunks])
                ],
                "verdict": {
                    "quality": f"✅ {len(chunks)} sections extracted"
                }
            }
            save_result(output_dir, "parser_markdown", result)
            
        except Exception as e:
            print(f"  ❌ Markdown splitter failed: {e}")
    
    else:
        print("▶ Plain text — no special parsing needed")
        result = {
            "parser": "plain_text",
            "file": file_path.name,
            "text_preview": content[:2000],
            "total_length": len(content),
            "verdict": {"quality": "✅ Plain text"}
        }
        save_result(output_dir, "parser_plain_text", result)