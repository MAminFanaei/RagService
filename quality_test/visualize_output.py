#!/usr/bin/env python3
# scripts/visualize_output.py
"""
Standalone visualization script for parser/chunker outputs.

Usage:
    # Visualize a JSON file containing parsed elements or chunks
    python scripts/visualize_output.py --input output.json --type parser
    python scripts/visualize_output.py --input chunks.json --type chunker
    
    # Compare multiple outputs
    python scripts/visualize_output.py --compare parser1.json parser2.json parser3.json
    
    # Visualize from pipeline directly (pass document path)
    python scripts/visualize_output.py --file document.pdf --run-parser pymupdf
    python scripts/visualize_output.py --file document.pdf --run-chunker fixed
    python scripts/visualize_output.py --file document.pdf --run-all
"""

import argparse
import asyncio
import html
import json
import os
import sys
import webbrowser
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add project root to path so we can import ingestion modules
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
OUTPUT_PATH = f"{PROJECT_ROOT}/quality_test/quality_test_output/"

# ══════════════════════════════════════════════════════════════════════════════
# HTML GENERATION
# ══════════════════════════════════════════════════════════════════════════════
def load_json_data(filepath: str) -> tuple[List[Dict], Optional[Dict]]:
    """
    Load JSON and extract data + metadata.
    Handles different JSON structures:
    - Direct list: [{"text": ...}, ...]
    - Wrapped object: {"chunks": [...], "metadata": {...}}
    - Test output format: {"chunker": "fixed", "chunks_preview": [...]}
    """
    with open(filepath, "r", encoding="utf-8") as f:
        raw = json.load(f)
    
    # Case 1: Direct list
    if isinstance(raw, list):
        return raw, None
    
    # Case 2: Dictionary - extract data and metadata
    if isinstance(raw, dict):
        # Try different keys for the data list
        data_keys = [
            "chunks",
            "elements", 
            "chunks_preview",
            "elements_preview",
            "items",
            "data",
        ]
        
        data = None
        for key in data_keys:
            if key in raw and isinstance(raw[key], list):
                data = raw[key]
                break
        
        # If no list found in known keys, check if any value is a list
        if data is None:
            for value in raw.values():
                if isinstance(value, list) and len(value) > 0:
                    data = value
                    break
        
        # Extract metadata (everything that's not the data list)
        metadata = {k: v for k, v in raw.items() if not isinstance(v, list)} if data else None
        
        if data is not None:
            return data, metadata
        
        # If still no list, maybe the dict itself is a single item?
        # Wrap it in a list
        return [raw], None
    
    # Unexpected type
    raise ValueError(f"Unexpected JSON structure: {type(raw)}")


def generate_html(
    data: List[Dict[str, Any]] | str,  # Accept filepath or data directly
    output_path: str,
    title: str = "Output Visualization",
    data_type: str = "auto",
    metadata: Optional[Dict[str, Any]] = None,
    open_browser: bool = True,
):
    """Generate beautiful HTML visualization from data."""
    
    # If data is a string, treat it as filepath
    if isinstance(data, str):
        data, loaded_metadata = load_json_data(data)
        if metadata is None:
            metadata = loaded_metadata
    
    # Ensure data is a list
    if not isinstance(data, list):
        print(f"⚠️  Warning: Data is not a list, got {type(data)}")
        if isinstance(data, dict):
            data = [data]
        else:
            raise ValueError(f"Data must be a list or filepath, got {type(data)}")
    
    # Handle empty list
    if len(data) == 0:
        print("⚠️  Warning: Empty data list")
        data = [{"text": "No data available", "element_type": "text"}]
    
    # Auto-detect data type
    if data_type == "auto":
        first_item = data[0]
        if "chunk_id" in first_item or "chunk_index" in first_item:
            data_type = "chunker"
        elif "element_type" in first_item:
            data_type = "parser"
        else:
            data_type = "generic"
    
    html_content = _build_full_html(data, title, data_type, metadata)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    abs_path = os.path.abspath(output_path)
    print(f"✅ Visualization saved: {abs_path}")
    
    if open_browser:
        webbrowser.open(f"file://{abs_path}")
    
    return abs_path


def generate_comparison_html(
    outputs: Dict[str, List[Dict[str, Any]]] | Dict[str, str],  # Accept filepaths too
    output_path: str,
    title: str = "Method Comparison",
    open_browser: bool = True,
):
    """Generate side-by-side comparison HTML."""
    
    # If values are strings (filepaths), load them
    loaded_outputs = {}
    for method_name, data_or_path in outputs.items():
        if isinstance(data_or_path, str):
            # It's a filepath
            data, _ = load_json_data(data_or_path)
            loaded_outputs[method_name] = data
        else:
            loaded_outputs[method_name] = data_or_path
    
    html_content = _build_comparison_html(loaded_outputs, title)
    
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_content)
    
    abs_path = os.path.abspath(output_path)
    print(f"✅ Comparison saved: {abs_path}")
    
    if open_browser:
        webbrowser.open(f"file://{abs_path}")
    
    return abs_path



# ══════════════════════════════════════════════════════════════════════════════
# HTML BUILDING HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _build_full_html(
    data: List[Dict],
    title: str,
    data_type: str,
    metadata: Optional[Dict],
) -> str:
    """Build complete HTML document."""
    
    parts = []
    parts.append(_html_head(title))
    parts.append(_css())
    parts.append("</head><body>")
    
    # Header
    parts.append(f'<div class="header">')
    parts.append(f'<h1>📄 {html.escape(title)}</h1>')
    parts.append(f'<p class="subtitle">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>')
    
    if metadata:
        parts.append('<div class="meta-badges">')
        for k, v in metadata.items():
            parts.append(f'<span class="badge">{html.escape(str(k))}: {html.escape(str(v))}</span>')
        parts.append('</div>')
    
    parts.append('</div>')
    
    # Controls
    parts.append(_controls_html(data_type))
    
    # Stats
    if data_type == "chunker":
        stats = _chunk_stats(data)
        parts.append(_stats_html_chunker(stats))
    else:
        stats = _element_stats(data)
        parts.append(_stats_html_parser(stats))
    
    # Content
    parts.append('<div class="content" id="content">')
    
    for idx, item in enumerate(data):
        if data_type == "chunker":
            parts.append(_render_chunk_card(item, idx))
        else:
            parts.append(_render_element_card(item, idx))
    
    parts.append('</div>')
    
    # Footer
    parts.append(f'<div class="footer">{len(data)} items | Click cards to expand</div>')
    
    # JavaScript
    parts.append(_javascript())
    parts.append("</body></html>")
    
    return "".join(parts)


def _build_comparison_html(outputs: Dict[str, List], title: str) -> str:
    """Build comparison HTML with tabs."""
    
    parts = []
    parts.append(_html_head(title))
    parts.append(_css())
    parts.append(_comparison_css())
    parts.append("</head><body>")
    
    # Header
    parts.append(f'<div class="header">')
    parts.append(f'<h1>🔀 {html.escape(title)}</h1>')
    parts.append(f'<p class="subtitle">{len(outputs)} methods compared</p>')
    parts.append('</div>')
    
    # Tabs
    parts.append('<div class="tabs">')
    for i, method_name in enumerate(outputs.keys()):
        active = "active" if i == 0 else ""
        safe_id = _safe_id(method_name)
        parts.append(f'<button class="tab {active}" onclick="showTab(\'{safe_id}\')" data-tab="{safe_id}">{html.escape(method_name)}</button>')
    parts.append('</div>')
    
    # Tab contents
    for i, (method_name, data) in enumerate(outputs.items()):
        safe_id = _safe_id(method_name)
        active = "active" if i == 0 else ""
        
        parts.append(f'<div class="tab-content {active}" id="{safe_id}">')
        
        # Detect type
        is_chunks = data and "chunk_id" in data[0]
        
        # Stats
        if is_chunks:
            stats = _chunk_stats(data)
            parts.append(_stats_html_chunker(stats))
        else:
            stats = _element_stats(data)
            parts.append(_stats_html_parser(stats))
        
        # Items
        parts.append('<div class="content">')
        for idx, item in enumerate(data):
            if is_chunks:
                parts.append(_render_chunk_card(item, idx))
            else:
                parts.append(_render_element_card(item, idx))
        parts.append('</div>')
        
        parts.append('</div>')
    
    # JavaScript
    parts.append(_javascript())
    parts.append(_comparison_javascript())
    parts.append("</body></html>")
    
    return "".join(parts)


def _html_head(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
"""


def _css() -> str:
    return """
<style>
:root {
    --primary: #6366f1;
    --primary-dark: #4f46e5;
    --success: #22c55e;
    --warning: #f59e0b;
    --error: #ef4444;
    --bg: #f8fafc;
    --card-bg: #ffffff;
    --text: #1e293b;
    --text-muted: #64748b;
    --border: #e2e8f0;
}

* { box-sizing: border-box; margin: 0; padding: 0; }

body {
    font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
    background: var(--bg);
    color: var(--text);
    line-height: 1.6;
}

.header {
    background: linear-gradient(135deg, var(--primary) 0%, var(--primary-dark) 100%);
    color: white;
    padding: 40px;
    text-align: center;
}

.header h1 {
    font-size: 2.5rem;
    font-weight: 700;
    margin-bottom: 8px;
}

.header .subtitle {
    opacity: 0.9;
    font-size: 1rem;
}

.meta-badges {
    display: flex;
    gap: 10px;
    justify-content: center;
    flex-wrap: wrap;
    margin-top: 20px;
}

.badge {
    background: rgba(255,255,255,0.2);
    padding: 6px 14px;
    border-radius: 20px;
    font-size: 0.85rem;
    backdrop-filter: blur(10px);
}

.controls {
    background: var(--card-bg);
    padding: 20px;
    border-bottom: 1px solid var(--border);
    display: flex;
    gap: 15px;
    flex-wrap: wrap;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 100;
}

.search-input {
    flex: 1;
    min-width: 250px;
    padding: 12px 16px;
    border: 2px solid var(--border);
    border-radius: 8px;
    font-size: 1rem;
    transition: border-color 0.2s;
}

.search-input:focus {
    outline: none;
    border-color: var(--primary);
}

.filter-group {
    display: flex;
    gap: 8px;
}

.filter-btn {
    padding: 8px 16px;
    border: 2px solid var(--border);
    background: var(--card-bg);
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.9rem;
    transition: all 0.2s;
}

.filter-btn:hover {
    border-color: var(--primary);
}

.filter-btn.active {
    background: var(--primary);
    color: white;
    border-color: var(--primary);
}

.stats {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
    gap: 15px;
    padding: 20px;
    background: var(--card-bg);
    border-bottom: 1px solid var(--border);
}

.stat-card {
    text-align: center;
    padding: 15px;
    background: var(--bg);
    border-radius: 8px;
}

.stat-value {
    font-size: 2rem;
    font-weight: 700;
    color: var(--primary);
}

.stat-label {
    font-size: 0.8rem;
    color: var(--text-muted);
    text-transform: uppercase;
    letter-spacing: 0.5px;
}

.content {
    padding: 20px;
    display: flex;
    flex-direction: column;
    gap: 15px;
}

.card {
    background: var(--card-bg);
    border: 1px solid var(--border);
    border-radius: 12px;
    overflow: hidden;
    transition: all 0.2s;
}

.card:hover {
    border-color: var(--primary);
    box-shadow: 0 4px 12px rgba(99, 102, 241, 0.15);
}

.card-header {
    display: flex;
    justify-content: space-between;
    align-items: center;
    padding: 15px 20px;
    background: var(--bg);
    border-bottom: 1px solid var(--border);
    cursor: pointer;
}

.card-header:hover {
    background: #f1f5f9;
}

.card-title {
    display: flex;
    align-items: center;
    gap: 12px;
}

.type-badge {
    padding: 4px 10px;
    border-radius: 4px;
    font-size: 0.75rem;
    font-weight: 600;
    text-transform: uppercase;
}

.type-heading { background: #fef3c7; color: #d97706; }
.type-text { background: #dbeafe; color: #2563eb; }
.type-table { background: #d1fae5; color: #059669; }
.type-image { background: #fce7f3; color: #db2777; }
.type-image_page { background: #fce7f3; color: #db2777; }

.card-index {
    color: var(--text-muted);
    font-size: 0.85rem;
    font-family: monospace;
}

.card-meta {
    display: flex;
    gap: 15px;
    flex-wrap: wrap;
    padding: 12px 20px;
    background: #fafafa;
    font-size: 0.85rem;
    color: var(--text-muted);
}

.meta-item {
    display: flex;
    align-items: center;
    gap: 5px;
}

.card-body {
    padding: 20px;
    display: none;
}

.card.expanded .card-body {
    display: block;
}

.text-content {
    background: var(--bg);
    padding: 15px;
    border-radius: 8px;
    font-family: 'Consolas', 'Monaco', monospace;
    font-size: 0.9rem;
    line-height: 1.7;
    white-space: pre-wrap;
    word-break: break-word;
    max-height: 400px;
    overflow-y: auto;
}

.text-content.rtl {
    direction: rtl;
    text-align: right;
}

.section-path {
    margin-top: 15px;
    padding: 10px 15px;
    background: #f0f9ff;
    border-radius: 6px;
    font-size: 0.85rem;
    color: #0369a1;
}

.section-path::before {
    content: "📂 ";
}

.raw-json {
    margin-top: 15px;
}

.raw-json summary {
    cursor: pointer;
    color: var(--text-muted);
    font-size: 0.85rem;
}

.raw-json pre {
    margin-top: 10px;
    background: #1e293b;
    color: #e2e8f0;
    padding: 15px;
    border-radius: 8px;
    overflow-x: auto;
    font-size: 0.8rem;
}

.footer {
    text-align: center;
    padding: 20px;
    color: var(--text-muted);
    font-size: 0.9rem;
}

/* Expand/collapse indicator */
.card-header::after {
    content: "▼";
    font-size: 0.7rem;
    color: var(--text-muted);
    transition: transform 0.2s;
}

.card.expanded .card-header::after {
    transform: rotate(180deg);
}

/* Animations */
@keyframes fadeIn {
    from { opacity: 0; transform: translateY(10px); }
    to { opacity: 1; transform: translateY(0); }
}

.card {
    animation: fadeIn 0.3s ease-out;
}

/* Print styles */
@media print {
    .controls, .stats { display: none; }
    .card-body { display: block !important; }
}
</style>
"""


def _comparison_css() -> str:
    return """
<style>
.tabs {
    display: flex;
    gap: 5px;
    padding: 15px 20px;
    background: var(--card-bg);
    border-bottom: 1px solid var(--border);
    overflow-x: auto;
}

.tab {
    padding: 10px 20px;
    border: none;
    background: var(--bg);
    border-radius: 6px;
    cursor: pointer;
    font-size: 0.95rem;
    font-weight: 500;
    transition: all 0.2s;
    white-space: nowrap;
}

.tab:hover {
    background: #e2e8f0;
}

.tab.active {
    background: var(--primary);
    color: white;
}

.tab-content {
    display: none;
}

.tab-content.active {
    display: block;
}
</style>
"""


def _controls_html(data_type: str) -> str:
    if data_type == "chunker":
        filters = """
            <button class="filter-btn active" onclick="filterType('all')">All</button>
            <button class="filter-btn" onclick="filterType('text')">Text</button>
            <button class="filter-btn" onclick="filterType('table')">Tables</button>
            <button class="filter-btn" onclick="filterType('heading')">Headings</button>
        """
    else:
        filters = """
            <button class="filter-btn active" onclick="filterType('all')">All</button>
            <button class="filter-btn" onclick="filterType('heading')">Headings</button>
            <button class="filter-btn" onclick="filterType('text')">Text</button>
            <button class="filter-btn" onclick="filterType('table')">Tables</button>
            <button class="filter-btn" onclick="filterType('image_page')">Images</button>
        """
    
    return f"""
<div class="controls">
    <input type="text" class="search-input" id="searchInput" placeholder="🔍 Search text, sections, metadata...">
    <div class="filter-group">
        {filters}
    </div>
    <button class="filter-btn" onclick="expandAll()">Expand All</button>
    <button class="filter-btn" onclick="collapseAll()">Collapse All</button>
</div>
"""


def _stats_html_parser(stats: Dict) -> str:
    return f"""
<div class="stats">
    <div class="stat-card">
        <div class="stat-value">{stats.get('total', 0)}</div>
        <div class="stat-label">Total Elements</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('pages', 0)}</div>
        <div class="stat-label">Pages</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('headings', 0)}</div>
        <div class="stat-label">Headings</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('tables', 0)}</div>
        <div class="stat-label">Tables</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('images', 0)}</div>
        <div class="stat-label">Image Pages</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('avg_length', 0)}</div>
        <div class="stat-label">Avg Length</div>
    </div>
</div>
"""


def _stats_html_chunker(stats: Dict) -> str:
    return f"""
<div class="stats">
    <div class="stat-card">
        <div class="stat-value">{stats.get('total', 0)}</div>
        <div class="stat-label">Total Chunks</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('avg_tokens', 0)}</div>
        <div class="stat-label">Avg Tokens</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('min_tokens', 0)}</div>
        <div class="stat-label">Min Tokens</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('max_tokens', 0)}</div>
        <div class="stat-label">Max Tokens</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('sections', 0)}</div>
        <div class="stat-label">Sections</div>
    </div>
    <div class="stat-card">
        <div class="stat-value">{stats.get('languages', 'N/A')}</div>
        <div class="stat-label">Languages</div>
    </div>
</div>
"""


def _render_element_card(elem: Dict, idx: int) -> str:
    elem_type = elem.get("element_type", "text")
    text = elem.get("text", "")
    page = elem.get("page_number", "?")
    section_title = elem.get("section_title", "")
    section_path = elem.get("section_path", [])
    heading_level = elem.get("heading_level")
    is_table = elem.get("is_table", False)
    language = elem.get("language_hint") or elem.get("language", "")
    
    # Direction
    direction = "rtl" if language in ["fa", "ar", "ur", "he"] else ""
    
    # Badge
    badge_class = f"type-{elem_type}"
    badge_text = elem_type
    if heading_level:
        badge_text = f"H{heading_level}"
    if is_table:
        badge_class = "type-table"
        badge_text = "TABLE"
    
    # Section path
    path_html = ""
    if section_path:
        path_str = " → ".join(html.escape(str(p)) for p in section_path)
        path_html = f'<div class="section-path">{path_str}</div>'
    
    # Search text
    search_text = f"{text} {section_title} {' '.join(str(p) for p in section_path)}".lower()
    
    return f"""
<div class="card" data-type="{elem_type}" data-search="{html.escape(search_text)}">
    <div class="card-header" onclick="toggleCard(this)">
        <div class="card-title">
            <span class="type-badge {badge_class}">{badge_text}</span>
            <span>{html.escape(section_title or text[:50] + ('...' if len(text) > 50 else ''))}</span>
        </div>
        <span class="card-index">#{idx + 1}</span>
    </div>
    <div class="card-meta">
        <span class="meta-item">📄 Page {page}</span>
        <span class="meta-item">📏 {len(text)} chars</span>
        {f'<span class="meta-item">🌐 {language}</span>' if language else ''}
    </div>
    <div class="card-body">
        <div class="text-content {direction}">{html.escape(text)}</div>
        {path_html}
        <details class="raw-json">
            <summary>View raw JSON</summary>
            <pre>{html.escape(json.dumps(elem, indent=2, ensure_ascii=False, default=str))}</pre>
        </details>
    </div>
</div>
"""


def _render_chunk_card(chunk: Dict, idx: int) -> str:
    text = chunk.get("text", "")
    chunk_id = chunk.get("chunk_id", "")
    section_title = chunk.get("section_title", "")
    page = chunk.get("page_number", "?")
    chunk_index = chunk.get("chunk_index", idx)
    token_count = chunk.get("token_count") or chunk.get("token_estimate", 0)
    element_type = chunk.get("element_type", "text")
    language = chunk.get("language", "")
    is_table = chunk.get("is_table", False)
    
    direction = "rtl" if language in ["fa", "ar", "ur", "he"] else ""
    
    badge_class = "type-table" if is_table else f"type-{element_type}"
    badge_text = "TABLE" if is_table else element_type.upper()
    
    search_text = f"{text} {section_title} {chunk_id}".lower()
    
    return f"""
<div class="card" data-type="{element_type}" data-search="{html.escape(search_text)}">
    <div class="card-header" onclick="toggleCard(this)">
        <div class="card-title">
            <span class="type-badge {badge_class}">{badge_text}</span>
            <span>{html.escape(section_title or text[:50] + ('...' if len(text) > 50 else ''))}</span>
        </div>
        <span class="card-index">Chunk #{chunk_index}</span>
    </div>
    <div class="card-meta">
        <span class="meta-item">📄 Page {page}</span>
        <span class="meta-item">🔤 {int(token_count)} tokens</span>
        <span class="meta-item">📏 {len(text)} chars</span>
        {f'<span class="meta-item">🌐 {language}</span>' if language else ''}
    </div>
    <div class="card-body">
        <div class="text-content {direction}">{html.escape(text)}</div>
        <details class="raw-json">
            <summary>View raw JSON</summary>
            <pre>{html.escape(json.dumps(chunk, indent=2, ensure_ascii=False, default=str))}</pre>
        </details>
    </div>
</div>
"""


def _javascript() -> str:
    return """
<script>
// Toggle card expansion
function toggleCard(header) {
    header.closest('.card').classList.toggle('expanded');
}

// Search
document.getElementById('searchInput')?.addEventListener('input', function(e) {
    const query = e.target.value.toLowerCase();
    document.querySelectorAll('.card').forEach(card => {
        const text = card.getAttribute('data-search') || '';
        card.style.display = text.includes(query) ? '' : 'none';
    });
});

// Filter by type
function filterType(type) {
    document.querySelectorAll('.filter-btn').forEach(btn => btn.classList.remove('active'));
    event.target.classList.add('active');
    
    document.querySelectorAll('.card').forEach(card => {
        if (type === 'all' || card.getAttribute('data-type') === type) {
            card.style.display = '';
        } else {
            card.style.display = 'none';
        }
    });
}

// Expand/collapse all
function expandAll() {
    document.querySelectorAll('.card').forEach(card => card.classList.add('expanded'));
}

function collapseAll() {
    document.querySelectorAll('.card').forEach(card => card.classList.remove('expanded'));
}
</script>
"""


def _comparison_javascript() -> str:
    return """
<script>
function showTab(tabId) {
    // Hide all tab contents
    document.querySelectorAll('.tab-content').forEach(tc => tc.classList.remove('active'));
    // Deactivate all tabs
    document.querySelectorAll('.tab').forEach(t => t.classList.remove('active'));
    // Show selected tab content
    document.getElementById(tabId).classList.add('active');
    // Activate selected tab
    document.querySelector(`[data-tab="${tabId}"]`).classList.add('active');
}
</script>
"""


# ══════════════════════════════════════════════════════════════════════════════
# STATS HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _element_stats(elements: List[Dict]) -> Dict:
    if not elements:
        return {"total": 0}
    
    pages = set()
    headings = 0
    tables = 0
    images = 0
    lengths = []
    
    for elem in elements:
        if elem.get("page_number"):
            pages.add(elem["page_number"])
        if elem.get("element_type") == "heading":
            headings += 1
        if elem.get("is_table"):
            tables += 1
        if elem.get("element_type") == "image_page":
            images += 1
        if elem.get("text"):
            lengths.append(len(elem["text"]))
    
    return {
        "total": len(elements),
        "pages": len(pages),
        "headings": headings,
        "tables": tables,
        "images": images,
        "avg_length": int(sum(lengths) / len(lengths)) if lengths else 0,
    }


def _chunk_stats(chunks: List[Dict]) -> Dict:
    if not chunks:
        return {"total": 0}
    
    tokens = []
    sections = set()
    languages = set()
    
    for chunk in chunks:
        tc = chunk.get("token_count") or chunk.get("token_estimate", 0)
        if tc:
            tokens.append(int(tc))
        if chunk.get("section_title"):
            sections.add(chunk["section_title"])
        if chunk.get("language"):
            languages.add(chunk["language"])
    
    return {
        "total": len(chunks),
        "avg_tokens": int(sum(tokens) / len(tokens)) if tokens else 0,
        "min_tokens": min(tokens) if tokens else 0,
        "max_tokens": max(tokens) if tokens else 0,
        "sections": len(sections),
        "languages": ", ".join(sorted(languages)) if languages else "N/A",
    }


def _safe_id(name: str) -> str:
    """Convert method name to safe HTML id."""
    return name.replace(" ", "_").replace(".", "_").replace("-", "_").lower()


# ══════════════════════════════════════════════════════════════════════════════
# PIPELINE RUNNERS
# ══════════════════════════════════════════════════════════════════════════════

async def run_parser(file_path: Path, parser_name: str) -> List[Dict]:
    """Run a specific parser on a file."""
    
    with open(file_path, "rb") as f:
        data = f.read()
    
    if parser_name == "pymupdf":
        from ingestion.parsers.pdf_pymupdf import PyMuPDFParser
        parser = PyMuPDFParser()
    elif parser_name == "docling":
        from ingestion.parsers.pdf_docling import DoclingParser
        parser = DoclingParser()
    elif parser_name == "python-docx":
        from ingestion.parsers.docx import DocxParser
        parser = DocxParser()
    else:
        raise ValueError(f"Unknown parser: {parser_name}")
    
    elements = await parser.parse(data, file_path.name)
    
    # Convert to dicts
    return [_element_to_dict(e) for e in elements]


def run_chunker(elements: List[Dict], chunker_name: str) -> List[Dict]:
    """Run a specific chunker on parsed elements."""
    
    if chunker_name == "fixed":
        from ingestion.chunkers.fixed import FixedChunker
        chunker = FixedChunker()
    elif chunker_name == "heading":
        from ingestion.chunkers.heading import HeadingChunker
        chunker = HeadingChunker()
    elif chunker_name == "sentence":
        from ingestion.chunkers.sentence import SentenceChunker
        chunker = SentenceChunker()
    elif chunker_name == "semantic":
        from ingestion.chunkers.semantic import SemanticChunker
        chunker = SemanticChunker()
    else:
        raise ValueError(f"Unknown chunker: {chunker_name}")
    
    return chunker.chunk(elements)


def _element_to_dict(elem) -> Dict:
    """Convert ParsedElement to dict."""
    if isinstance(elem, dict):
        return elem
    return {
        "text": getattr(elem, "text", ""),
        "element_type": getattr(elem, "element_type", "text"),
        "page_number": getattr(elem, "page_number", None),
        "bounding_box": getattr(elem, "bounding_box", None),
        "section_title": getattr(elem, "section_title", None),
        "section_path": getattr(elem, "section_path", []),
        "heading_level": getattr(elem, "heading_level", None),
        "is_table": getattr(elem, "is_table", False),
        "table_markdown": getattr(elem, "table_markdown", None),
        "language_hint": getattr(elem, "language_hint", None),
        "parser_name": getattr(elem, "parser_name", "unknown"),
        "raw_metadata": getattr(elem, "raw_metadata", {}),
    }


# ══════════════════════════════════════════════════════════════════════════════
# CLI
# ══════════════════════════════════════════════════════════════════════════════
def main():
    parser = argparse.ArgumentParser(
        description="Visualize parser/chunker outputs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # From JSON file
  python visualize_output.py --input chunks.json
  
  # Compare multiple JSON files
  python visualize_output.py --compare parser1.json parser2.json
  
  # Run parser on document
  python visualize_output.py --file doc.pdf --run-parser pymupdf
  
  # Run full pipeline
  python visualize_output.py --file doc.pdf --run-all
        """
    )
    
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=str, help="JSON file with elements/chunks")
    group.add_argument("--compare", nargs="+", help="Multiple JSON files to compare")
    group.add_argument("--file", type=str, help="Document file to process")
    
    parser.add_argument("--type", choices=["parser", "chunker", "auto"], default="auto",
                        help="Data type (default: auto-detect)")
    parser.add_argument("--output", type=str, default=f"visualization.html",
                        help="Output HTML file")
    parser.add_argument("--no-browser", action="store_true",
                        help="Don't open in browser")
    
    # Pipeline options
    parser.add_argument("--run-parser", type=str, choices=["pymupdf", "docling", "python-docx"],
                        help="Parser to run")
    parser.add_argument("--run-chunker", type=str, choices=["fixed", "heading", "sentence", "semantic"],
                        help="Chunker to run")
    parser.add_argument("--run-all", action="store_true",
                        help="Run parser + chunker pipeline")
    
    args = parser.parse_args()
    
    # ── Load from JSON ────────────────────────────────────────────────────────
    if args.input:
        print(f"📂 Loading: {args.input}")
        
        try:
            data, metadata = load_json_data(args.input)
            
            # Add file info to metadata
            if metadata is None:
                metadata = {}
            metadata["source_file"] = args.input
            metadata["loaded_items"] = len(data)
            
            generate_html(
                data=data,
                output_path=OUTPUT_PATH+args.output,
                title=f"Visualization: {Path(args.input).stem}",
                data_type=args.type,
                metadata=metadata,
                open_browser=not args.no_browser,
            )
        except Exception as e:
            print(f"❌ Error loading JSON: {e}")
            import traceback
            traceback.print_exc()
            sys.exit(1)
    
    # ── Compare multiple files ────────────────────────────────────────────────
    elif args.compare:
        outputs = {}
        for filepath in args.compare:
            print(f"📂 Loading: {filepath}")
            try:
                data, _ = load_json_data(filepath)
                outputs[Path(filepath).stem] = data
            except Exception as e:
                print(f"⚠️  Failed to load {filepath}: {e}")
        
        if not outputs:
            print("❌ No valid files loaded")
            sys.exit(1)
        
        generate_comparison_html(
            outputs=outputs,
            output_path=OUTPUT_PATH+args.output,
            title="Method Comparison",
            open_browser=not args.no_browser,
        )
    
    # ── Run pipeline on document ──────────────────────────────────────────────
    elif args.file:
        file_path = Path(args.file)
        if not file_path.exists():
            print(f"❌ File not found: {file_path}")
            sys.exit(1)
        
        if args.run_all or args.run_parser:
            parser_name = args.run_parser or "pymupdf"
            print(f"🔄 Running parser: {parser_name}")
            elements = asyncio.run(run_parser(file_path, parser_name))
            print(f"   ✅ Got {len(elements)} elements")
            
            # Save intermediate
            parser_output = args.output.replace(".html", "_parser.json")
            with open(parser_output, "w", encoding="utf-8") as f:
                json.dump(elements, f, indent=2, ensure_ascii=False, default=str)
            print(f"   💾 Saved: {parser_output}")
            
            if args.run_all or args.run_chunker:
                chunker_name = args.run_chunker or "fixed"
                print(f"🔄 Running chunker: {chunker_name}")
                chunks = run_chunker(elements, chunker_name)
                print(f"   ✅ Got {len(chunks)} chunks")
                
                # Save chunks
                chunker_output = args.output.replace(".html", "_chunks.json")
                with open(chunker_output, "w", encoding="utf-8") as f:
                    json.dump(chunks, f, indent=2, ensure_ascii=False, default=str)
                print(f"   💾 Saved: {chunker_output}")
                
                # Visualize chunks
                generate_html(
                    data=chunks,
                    output_path=OUTPUT_PATH+args.output,
                    title=f"Chunks: {file_path.name}",
                    data_type="chunker",
                    metadata={"file": file_path.name, "parser": parser_name, "chunker": chunker_name},
                    open_browser=not args.no_browser,
                )
            else:
                # Visualize parser output only
                generate_html(
                    data=elements,
                    output_path=OUTPUT_PATH+args.output,
                    title=f"Parsed: {file_path.name}",
                    data_type="parser",
                    metadata={"file": file_path.name, "parser": parser_name},
                    open_browser=not args.no_browser,
                )
        else:
            print("❌ Specify --run-parser, --run-chunker, or --run-all")
            sys.exit(1)


if __name__ == "__main__":
    main()

"""# Save the script
mkdir -p scripts
# Copy the code above to scripts/visualize_output.py

# ══════════════════════════════════════════════════════════════════════════════
# OPTION 1: Visualize existing JSON output
# ══════════════════════════════════════════════════════════════════════════════

# Visualize parser output (auto-detects type)
python scripts/visualize_output.py --input parser_output.json

# Visualize chunker output
python scripts/visualize_output.py --input chunks.json --type chunker

# Specify output file
python scripts/visualize_output.py --input data.json --output my_vis.html

# ══════════════════════════════════════════════════════════════════════════════
# OPTION 2: Compare multiple outputs
# ══════════════════════════════════════════════════════════════════════════════

# Compare different parsers
python scripts/visualize_output.py --compare \
    parser_pymupdf.json \
    parser_docling.json \
    --output parser_comparison.html

# Compare different chunkers
python scripts/visualize_output.py --compare \
    chunker_fixed.json \
    chunker_semantic.json \
    chunker_sentence.json \
    --output chunker_comparison.html

# ══════════════════════════════════════════════════════════════════════════════
# OPTION 3: Run pipeline directly on a document
# ══════════════════════════════════════════════════════════════════════════════

# Run just parser
python scripts/visualize_output.py --file document.pdf --run-parser pymupdf

# Run parser + chunker
python scripts/visualize_output.py --file document.pdf --run-parser pymupdf --run-chunker fixed

# Run full pipeline with defaults (pymupdf + fixed)
python scripts/visualize_output.py --file document.pdf --run-all

# Run with specific options
python scripts/visualize_output.py \
    --file document.pdf \
    --run-parser docling \
    --run-chunker semantic \
    --output results.html

# ══════════════════════════════════════════════════════════════════════════════
# OPTION 4: Don't open browser (for CI/scripts)
# ══════════════════════════════════════════════════════════════════════════════

python scripts/visualize_output.py --input chunks.json --no-browser

# ══════════════════════════════════════════════════════════════════════════════
# If You Already Have JSON Output
# ══════════════════════════════════════════════════════════════════════════════
If your pipeline already saves JSON, just point to it:

Bash

# Your pipeline saves output
python your_pipeline.py --file doc.pdf --output pipeline_output.json

# Visualize it
python scripts/visualize_output.py --input pipeline_output.json
"""