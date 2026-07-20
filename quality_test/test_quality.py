# test_quality.py
# Usage:
#   python ./quality_test/test_quality.py --file mydoc.pdf --output results/
#   python ./quality_test/test_quality.py --file contract.docx --output results/
#   python ./quality_test/test_quality.py --file report.xlsx --output results/
#
# Each method writes its output to a separate JSON file you can compare

import argparse
import json
import sys
from pathlib import Path
from datetime import datetime

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", required=True, help="Your document to test")
    parser.add_argument("--output", default="quality_test_output", help="Output directory")
    parser.add_argument("--parsers", nargs="+", default=["all"], 
                        choices=["all", "pymupdf", "docling", "python-docx"])
    parser.add_argument("--chunkers", nargs="+", default=["all"],
                        choices=["all", "fixed", "heading", "sentence", "semantic", "token"])
    parser.add_argument("--max-chunks", type=int, default=20,
                        help="Max chunks to save per method (to avoid huge files)")
    args = parser.parse_args()

    file_path = Path(args.file)
    if not file_path.exists():
        print(f"❌ File not found: {file_path}")
        sys.exit(1)

    output_dir = Path(args.output)
    output_dir.mkdir(exist_ok=True, parents=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    session_dir = output_dir / f"{file_path.stem}_{timestamp}"
    session_dir.mkdir(exist_ok=True)

    print(f"\n{'═'*70}")
    print(f"  QUALITY TEST SESSION")
    print(f"{'═'*70}")
    print(f"  Input:  {file_path}")
    print(f"  Output: {session_dir}")
    print(f"{'═'*70}\n")

    # Detect file type
    suffix = file_path.suffix.lower()
    
    if suffix == ".pdf":
        from test_quality_pdf import run_pdf_tests
        run_pdf_tests(file_path, session_dir, args)
    elif suffix == ".docx":
        from test_quality_docx import run_docx_tests
        run_docx_tests(file_path, session_dir, args)
    elif suffix in [".xlsx", ".xls"]:
        from test_quality_xlsx import run_xlsx_tests
        run_xlsx_tests(file_path, session_dir, args)
    elif suffix in [".pptx", ".ppt"]:
        from test_quality_pptx import run_pptx_tests
        run_pptx_tests(file_path, session_dir, args)
    elif suffix in [".html", ".htm"]:
        from test_quality_html import run_html_tests
        run_html_tests(file_path, session_dir, args)
    elif suffix in [".txt", ".md"]:
        from test_quality_text import run_text_tests
        run_text_tests(file_path, session_dir, args)
    else:
        print(f"❌ Unsupported file type: {suffix}")
        sys.exit(1)

    print(f"\n{'═'*70}")
    print(f"  ✅ Results saved to: {session_dir}")
    print(f"  📂 Open the JSON files to compare outputs")
    print(f"{'═'*70}\n")

if __name__ == "__main__":
    main()