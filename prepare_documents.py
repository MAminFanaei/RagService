#!/usr/bin/env python3
"""
Prepare documents for RAG Service - Copy this from your notebook
This script should process your .docx files and create JSON chunks
"""

import json
import os
from pathlib import Path

# Copy your DocumentChunker class here from the notebook
# Then use it to process your documents

def main():
    """
    Example workflow:
    
    1. Place your .docx files in ./docs/main/
    2. Run this script to chunk them
    3. JSON files will be created in ./docs/main/
    """
    
    print("=" * 60)
    print("Document Preparation Script")
    print("=" * 60)
    
    # TODO: Copy your DocumentChunker class from notebook
    # from your_notebook import DocumentChunker
    
    # Example usage (adapt from your notebook):
    """
    chunker = DocumentChunker(
        min_tokens=360,
        max_tokens=900,
        chunk_overlap=0,
        tolerance=0.3,
        ignore_styles=["Caption"]
    )
    
    DOC_PATH = "./docs/main/"
    doc_files = [f for f in Path(DOC_PATH).glob("*.docx")]
    
    for doc_file in doc_files:
        print(f"Processing {doc_file.name}...")
        
        # Chunk the document
        sections = chunker.build_index_from_doc(
            book_path=str(doc_file),
            book_name=doc_file.stem
        )
        
        # Save to JSON
        output_file = DOC_PATH + doc_file.stem + ".json"
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(sections, f, ensure_ascii=False, indent=2)
        
        print(f"  ✓ Created {output_file} with {len(sections)} chunks")
    
    print(f"\n✓ Processed {len(doc_files)} documents")
    """
    
    print("\n⚠️  This is a template script!")
    print("Copy your DocumentChunker class from the notebook and adapt this script.")
    print("\nExpected JSON format for each chunk:")
    print(json.dumps({
        "book_name": "document_name",
        "section_title": "Section Title",
        "chunk_text": "The actual text content...",
        "chunk_id": "unique-id",
        "chunk_order": 1,
        "token_count": 450,
        "h1": {"section_title": "Chapter 1", "section_id": "id1"},
        "h2": {"section_title": "Section 1.1", "section_id": "id2"},
        "heading_path": "Chapter 1 -> Section 1.1"
    }, indent=2))


if __name__ == "__main__":
    main()