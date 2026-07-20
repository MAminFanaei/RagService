# test_quality_html.py

import json
import time
from pathlib import Path

def save_result(output_path, method, data):
    with open(output_path / f"{method}.json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    print(f"  ✅ {method:<20} → {output_path / f'{method}.json'}")

def run_html_tests(file_path: Path, output_dir: Path, args):
    print(f"🌐 Testing HTML parser on: {file_path.name}\n")
    
    with open(file_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    # ── Trafilatura ───────────────────────────────────────────────────────────
    print("▶ Testing Trafilatura...")
    try:
        import trafilatura
        
        t0 = time.perf_counter()
        main_content = trafilatura.extract(html_content, include_tables=True, include_links=False)
        elapsed = time.perf_counter() - t0
        
        result = {
            "parser": "trafilatura",
            "file": file_path.name,
            "time_seconds": round(elapsed, 3),
            "extracted_text_preview": (main_content or "")[:2000],
            "extracted_length": len(main_content or ""),
            "verdict": {
                "quality": "✅ Main content extracted" if main_content else "❌ No content found"
            }
        }
        save_result(output_dir, "parser_trafilatura", result)
        
    except ImportError:
        print(f"  ⏭️  Trafilatura not installed — skip")
    except Exception as e:
        print(f"  ❌ Trafilatura failed: {e}")
    
    # ── BeautifulSoup ─────────────────────────────────────────────────────────
    print("▶ Testing BeautifulSoup...")
    try:
        from bs4 import BeautifulSoup
        
        t0 = time.perf_counter()
        soup = BeautifulSoup(html_content, "lxml")
        
        # Extract headings
        headings = []
        for tag in ["h1", "h2", "h3", "h4", "h5", "h6"]:
            for elem in soup.find_all(tag):
                headings.append({
                    "level": int(tag[1]),
                    "text": elem.get_text(strip=True),
                })
        
        # Extract paragraphs
        paragraphs = [p.get_text(strip=True) for p in soup.find_all("p") if p.get_text(strip=True)]
        
        elapsed = time.perf_counter() - t0
        
        result = {
            "parser": "beautifulsoup",
            "file": file_path.name,
            "time_seconds": round(elapsed, 3),
            "heading_count": len(headings),
            "paragraph_count": len(paragraphs),
            "headings_preview": headings[:20],
            "paragraphs_preview": paragraphs[:10],
            "verdict": {
                "quality": "✅ Structure preserved" if headings else "⚠️ No headings"
            }
        }
        save_result(output_dir, "parser_beautifulsoup", result)
        
    except Exception as e:
        print(f"  ❌ BeautifulSoup failed: {e}")