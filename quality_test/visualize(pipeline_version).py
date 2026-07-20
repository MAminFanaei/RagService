# ingestion/visualize.py
"""
Advanced visualization for parser/chunker outputs.
Creates beautiful, interactive HTML reports for quality inspection.
"""

import html
import json
import os
import webbrowser
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from collections import OrderedDict


def visualize_parser_output(
    elements: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    outpath: str = "parser_output.html",
    open_in_browser: bool = True,
    title: str = "Parser Output",
):
    """
    Visualize parsed elements (before chunking).
    
    Args:
        elements: List of ParsedElement dicts with keys:
            - text, element_type, page_number, bounding_box,
              section_title, section_path, heading_level, is_table, etc.
        metadata: Optional parser metadata (parser_name, file_name, etc.)
        outpath: Output HTML file path
        open_in_browser: Auto-open in browser
        title: Report title
    """
    
    html_parts = [_html_header(title)]
    html_parts.append(_build_css())
    html_parts.append("</head><body>")
    
    # Header
    html_parts.append(f'<div class="header">')
    html_parts.append(f'<h1>{html.escape(title)}</h1>')
    if metadata:
        html_parts.append('<div class="metadata">')
        for k, v in metadata.items():
            html_parts.append(f'<span class="meta-badge">{html.escape(str(k))}: {html.escape(str(v))}</span>')
        html_parts.append('</div>')
    html_parts.append('</div>')
    
    # Search and filters
    html_parts.append(_build_filters())
    
    # Stats summary
    stats = _compute_stats(elements)
    html_parts.append(_build_stats_panel(stats))
    
    # Main content
    html_parts.append('<div class="main-content">')
    html_parts.append('<div class="elements-container">')
    
    for idx, elem in enumerate(elements):
        html_parts.append(_render_element(elem, idx))
    
    html_parts.append('</div>')  # elements-container
    html_parts.append('</div>')  # main-content
    
    # Footer
    html_parts.append(f'<div class="footer">Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | {len(elements)} elements</div>')
    
    html_parts.append(_build_js())
    html_parts.append("</body></html>")
    
    # Write file
    with open(outpath, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))
    
    abs_path = os.path.abspath(outpath)
    print(f"✅ Parser visualization: {abs_path}")
    if open_in_browser:
        webbrowser.open("file://" + abs_path)


def visualize_chunker_output(
    chunks: List[Dict[str, Any]],
    metadata: Optional[Dict[str, Any]] = None,
    outpath: str = "chunker_output.html",
    open_in_browser: bool = True,
    title: str = "Chunker Output",
    compare_mode: bool = False,
):
    """
    Visualize chunked output (after chunking).
    
    Args:
        chunks: List of chunk dicts with keys:
            - text, chunk_id, doc_id, section_title, page_number,
              chunk_index, token_count, element_type, etc.
        metadata: Optional chunker metadata (chunker_name, strategy, etc.)
        outpath: Output HTML file path
        open_in_browser: Auto-open in browser
        title: Report title
        compare_mode: If True, enables side-by-side comparison view
    """
    
    html_parts = [_html_header(title)]
    html_parts.append(_build_css(chunker_mode=True))
    html_parts.append("</head><body>")
    
    # Header
    html_parts.append(f'<div class="header">')
    html_parts.append(f'<h1>{html.escape(title)}</h1>')
    if metadata:
        html_parts.append('<div class="metadata">')
        for k, v in metadata.items():
            html_parts.append(f'<span class="meta-badge">{html.escape(str(k))}: {html.escape(str(v))}</span>')
        html_parts.append('</div>')
    html_parts.append('</div>')
    
    # Search and filters
    html_parts.append(_build_filters(chunker_mode=True))
    
    # Stats summary
    stats = _compute_chunk_stats(chunks)
    html_parts.append(_build_chunk_stats_panel(stats))
    
    # Main content
    html_parts.append('<div class="main-content">')
    
    if compare_mode:
        html_parts.append('<div class="compare-container">')
        # Group by section for comparison
        sections = _group_by_section(chunks)
        for section_title, section_chunks in sections.items():
            html_parts.append(_render_section_group(section_title, section_chunks))
        html_parts.append('</div>')
    else:
        html_parts.append('<div class="chunks-container">')
        for idx, chunk in enumerate(chunks):
            html_parts.append(_render_chunk(chunk, idx))
        html_parts.append('</div>')
    
    html_parts.append('</div>')  # main-content
    
    # Footer
    html_parts.append(f'<div class="footer">Generated {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} | {len(chunks)} chunks</div>')
    
    html_parts.append(_build_js(chunker_mode=True))
    html_parts.append("</body></html>")
    
    # Write file
    with open(outpath, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))
    
    abs_path = os.path.abspath(outpath)
    print(f"✅ Chunker visualization: {abs_path}")
    if open_in_browser:
        webbrowser.open("file://" + abs_path)


def visualize_comparison(
    outputs: Dict[str, List[Dict[str, Any]]],
    outpath: str = "comparison.html",
    open_in_browser: bool = True,
    title: str = "Method Comparison",
):
    """
    Compare multiple parser/chunker outputs side-by-side.
    
    Args:
        outputs: Dict of {method_name: list_of_chunks/elements}
        outpath: Output HTML file path
        open_in_browser: Auto-open in browser
        title: Report title
    """
    
    html_parts = [_html_header(title)]
    html_parts.append(_build_css(comparison_mode=True))
    html_parts.append("</head><body>")
    
    html_parts.append(f'<div class="header"><h1>{html.escape(title)}</h1></div>')
    
    # Method selector tabs
    html_parts.append('<div class="tabs">')
    for method_name in outputs.keys():
        safe_id = method_name.replace(" ", "_").lower()
        html_parts.append(f'<button class="tab-button" onclick="showTab(\'{safe_id}\')">{html.escape(method_name)}</button>')
    html_parts.append('</div>')
    
    # Tab content
    for method_name, data in outputs.items():
        safe_id = method_name.replace(" ", "_").lower()
        html_parts.append(f'<div id="{safe_id}" class="tab-content">')
        
        stats = _compute_chunk_stats(data) if data and "chunk_id" in data[0] else _compute_stats(data)
        html_parts.append(_build_stats_panel(stats) if "chunk_id" not in data[0] else _build_chunk_stats_panel(stats))
        
        html_parts.append('<div class="elements-container">')
        for idx, item in enumerate(data):
            if "chunk_id" in item:
                html_parts.append(_render_chunk(item, idx))
            else:
                html_parts.append(_render_element(item, idx))
        html_parts.append('</div>')
        
        html_parts.append('</div>')  # tab-content
    
    html_parts.append(_build_js(comparison_mode=True))
    html_parts.append("</body></html>")
    
    with open(outpath, "w", encoding="utf-8") as f:
        f.write("".join(html_parts))
    
    abs_path = os.path.abspath(outpath)
    print(f"✅ Comparison visualization: {abs_path}")
    if open_in_browser:
        webbrowser.open("file://" + abs_path)


# ── Internal rendering helpers ────────────────────────────────────────────────

def _html_header(title: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{html.escape(title)}</title>
"""


def _build_css(chunker_mode=False, comparison_mode=False) -> str:
    base_css = """
    <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    
    body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, 'Noto Sans', sans-serif;
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 20px;
        color: #333;
    }
    
    .header {
        background: white;
        border-radius: 12px;
        padding: 30px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    h1 {
        font-size: 32px;
        font-weight: 700;
        color: #1a202c;
        margin-bottom: 10px;
    }
    
    .metadata {
        display: flex;
        gap: 10px;
        flex-wrap: wrap;
        margin-top: 15px;
    }
    
    .meta-badge {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        padding: 6px 12px;
        border-radius: 20px;
        font-size: 13px;
        font-weight: 500;
    }
    
    .filters {
        background: white;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .search-box {
        width: 100%;
        padding: 12px 20px;
        border: 2px solid #e2e8f0;
        border-radius: 8px;
        font-size: 16px;
        transition: border-color 0.3s;
    }
    
    .search-box:focus {
        outline: none;
        border-color: #667eea;
    }
    
    .filter-buttons {
        display: flex;
        gap: 10px;
        margin-top: 15px;
        flex-wrap: wrap;
    }
    
    .filter-btn {
        padding: 8px 16px;
        border: 2px solid #e2e8f0;
        background: white;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
        transition: all 0.3s;
    }
    
    .filter-btn:hover {
        border-color: #667eea;
        background: #f7fafc;
    }
    
    .filter-btn.active {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
        border-color: transparent;
    }
    
    .stats-panel {
        background: white;
        border-radius: 12px;
        padding: 20px;
        margin-bottom: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .stats-grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        gap: 15px;
        margin-top: 15px;
    }
    
    .stat-card {
        background: linear-gradient(135deg, #f7fafc 0%, #edf2f7 100%);
        padding: 15px;
        border-radius: 8px;
        border-left: 4px solid #667eea;
    }
    
    .stat-value {
        font-size: 28px;
        font-weight: 700;
        color: #667eea;
        margin-bottom: 5px;
    }
    
    .stat-label {
        font-size: 13px;
        color: #718096;
        text-transform: uppercase;
        letter-spacing: 0.5px;
    }
    
    .main-content {
        background: white;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
        max-height: calc(100vh - 500px);
        overflow-y: auto;
    }
    
    .element-card, .chunk-card {
        border: 2px solid #e2e8f0;
        border-radius: 8px;
        padding: 20px;
        margin-bottom: 15px;
        transition: all 0.3s;
        position: relative;
    }
    
    .element-card:hover, .chunk-card:hover {
        border-color: #667eea;
        box-shadow: 0 4px 12px rgba(102, 126, 234, 0.2);
    }
    
    .element-header, .chunk-header {
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 12px;
        padding-bottom: 12px;
        border-bottom: 1px solid #e2e8f0;
    }
    
    .element-type-badge {
        padding: 4px 12px;
        border-radius: 4px;
        font-size: 12px;
        font-weight: 600;
        text-transform: uppercase;
    }
    
    .type-heading { background: #fef5e7; color: #d68910; }
    .type-text { background: #e8f4fd; color: #1a73e8; }
    .type-table { background: #e8f5e9; color: #2e7d32; }
    .type-image { background: #fce4ec; color: #c2185b; }
    .type-footer { background: #f3e5f5; color: #7b1fa2; }
    
    .element-meta, .chunk-meta {
        display: flex;
        gap: 15px;
        flex-wrap: wrap;
        font-size: 13px;
        color: #718096;
        margin-bottom: 12px;
    }
    
    .meta-item {
        display: flex;
        align-items: center;
        gap: 5px;
    }
    
    .meta-icon {
        opacity: 0.6;
    }
    
    .element-text, .chunk-text {
        background: #f7fafc;
        padding: 15px;
        border-radius: 6px;
        border-left: 4px solid #cbd5e0;
        font-family: 'Consolas', 'Monaco', monospace;
        font-size: 14px;
        line-height: 1.6;
        white-space: pre-wrap;
        word-break: break-word;
        direction: auto;
    }
    
    .rtl { direction: rtl; }
    .ltr { direction: ltr; }
    
    .section-path {
        background: #edf2f7;
        padding: 8px 12px;
        border-radius: 6px;
        font-size: 13px;
        color: #4a5568;
        margin-top: 10px;
        font-family: monospace;
    }
    
    .bounding-box {
        position: absolute;
        top: 10px;
        right: 10px;
        background: rgba(102, 126, 234, 0.1);
        padding: 4px 8px;
        border-radius: 4px;
        font-size: 11px;
        font-family: monospace;
        color: #667eea;
    }
    
    .footer {
        background: white;
        border-radius: 12px;
        padding: 15px;
        margin-top: 20px;
        text-align: center;
        color: #718096;
        font-size: 13px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .tabs {
        background: white;
        border-radius: 12px;
        padding: 10px;
        margin-bottom: 20px;
        display: flex;
        gap: 10px;
        box-shadow: 0 4px 6px rgba(0,0,0,0.1);
    }
    
    .tab-button {
        padding: 10px 20px;
        border: none;
        background: #f7fafc;
        border-radius: 6px;
        cursor: pointer;
        font-size: 14px;
        font-weight: 500;
        transition: all 0.3s;
    }
    
    .tab-button:hover {
        background: #edf2f7;
    }
    
    .tab-button.active {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        color: white;
    }
    
    .tab-content {
        display: none;
    }
    
    .tab-content.active {
        display: block;
    }
    
    /* Scrollbar styling */
    ::-webkit-scrollbar {
        width: 8px;
        height: 8px;
    }
    
    ::-webkit-scrollbar-track {
        background: #f1f1f1;
        border-radius: 10px;
    }
    
    ::-webkit-scrollbar-thumb {
        background: #667eea;
        border-radius: 10px;
    }
    
    ::-webkit-scrollbar-thumb:hover {
        background: #764ba2;
    }
    
    @media print {
        body { background: white; }
        .filters, .stats-panel { display: none; }
    }
    </style>
    """
    return base_css


def _build_filters(chunker_mode=False) -> str:
    filter_types = ["heading", "text", "table", "image"] if not chunker_mode else ["text", "table", "heading"]
    
    buttons_html = "".join([
        f'<button class="filter-btn" onclick="filterByType(\'{t}\')">{t.title()}</button>'
        for t in filter_types
    ])
    
    return f"""
    <div class="filters">
        <input type="text" class="search-box" id="searchBox" placeholder="🔍 Search in text, metadata, section titles..." />
        <div class="filter-buttons">
            <button class="filter-btn active" onclick="filterByType('all')">All</button>
            {buttons_html}
        </div>
    </div>
    """


def _build_stats_panel(stats: Dict[str, Any]) -> str:
    return f"""
    <div class="stats-panel">
        <h2>Statistics</h2>
        <div class="stats-grid">
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
                <div class="stat-label">Images</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('avg_text_length', 0)}</div>
                <div class="stat-label">Avg Text Length</div>
            </div>
        </div>
    </div>
    """


def _build_chunk_stats_panel(stats: Dict[str, Any]) -> str:
    return f"""
    <div class="stats-panel">
        <h2>Chunk Statistics</h2>
        <div class="stats-grid">
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
                <div class="stat-label">Unique Sections</div>
            </div>
            <div class="stat-card">
                <div class="stat-value">{stats.get('languages', 'N/A')}</div>
                <div class="stat-label">Languages</div>
            </div>
        </div>
    </div>
    """


def _render_element(elem: Dict[str, Any], idx: int) -> str:
    elem_type = elem.get("element_type", "text")
    text = elem.get("text", "")
    page = elem.get("page_number", "N/A")
    section_title = elem.get("section_title", "")
    section_path = elem.get("section_path", [])
    heading_level = elem.get("heading_level")
    is_table = elem.get("is_table", False)
    bbox = elem.get("bounding_box")
    language = elem.get("language_hint", "auto")
    
    # Determine direction
    direction = "rtl" if language in ["fa", "ar", "ur", "he"] else "ltr"
    
    # Build badge
    badge_class = f"type-{elem_type}"
    badge_text = elem_type
    if heading_level:
        badge_text = f"H{heading_level}"
    if is_table:
        badge_class = "type-table"
        badge_text = "TABLE"
    
    # Build metadata
    meta_items = [
        f'<span class="meta-item"><span class="meta-icon">📄</span>Page {page}</span>',
        f'<span class="meta-item"><span class="meta-icon">📏</span>{len(text)} chars</span>',
    ]
    
    if section_title:
        meta_items.append(f'<span class="meta-item"><span class="meta-icon">📍</span>{html.escape(section_title)}</span>')
    
    # Bounding box overlay
    bbox_html = ""
    if bbox:
        bbox_html = f'<div class="bounding-box">x:{bbox.get("x0", 0):.0f} y:{bbox.get("y0", 0):.0f}</div>'
    
    # Section path breadcrumb
    path_html = ""
    if section_path:
        path_str = " → ".join(html.escape(str(p)) for p in section_path)
        path_html = f'<div class="section-path">📂 {path_str}</div>'
    
    return f"""
    <div class="element-card" data-type="{elem_type}" data-index="{idx}" data-search="{html.escape(text.lower())} {html.escape(section_title.lower())}">
        {bbox_html}
        <div class="element-header">
            <span class="element-type-badge {badge_class}">{badge_text}</span>
            <span style="color: #a0aec0; font-size: 12px;">#{idx + 1}</span>
        </div>
        <div class="element-meta">
            {" ".join(meta_items)}
        </div>
        <div class="element-text {direction}">{html.escape(text)}</div>
        {path_html}
    </div>
    """


def _render_chunk(chunk: Dict[str, Any], idx: int) -> str:
    text = chunk.get("text", "")
    chunk_id = chunk.get("chunk_id", idx)
    section_title = chunk.get("section_title", "")
    page = chunk.get("page_number", "N/A")
    chunk_index = chunk.get("chunk_index", idx)
    token_count = chunk.get("token_count") or chunk.get("token_estimate", len(text.split()) * 1.3)
    element_type = chunk.get("element_type", "text")
    language = chunk.get("language", "auto")
    is_table = chunk.get("is_table", False)
    
    direction = "rtl" if language in ["fa", "ar", "ur", "he"] else "ltr"
    
    badge_class = "type-table" if is_table else f"type-{element_type}"
    badge_text = "TABLE" if is_table else element_type.upper()
    
    meta_items = [
        f'<span class="meta-item"><span class="meta-icon">🔢</span>Chunk {chunk_index}</span>',
        f'<span class="meta-item"><span class="meta-icon">📄</span>Page {page}</span>',
        f'<span class="meta-item"><span class="meta-icon">🔤</span>{int(token_count)} tokens</span>',
        f'<span class="meta-item"><span class="meta-icon">🌐</span>{language}</span>',
    ]
    
    if section_title:
        meta_items.append(f'<span class="meta-item"><span class="meta-icon">📍</span>{html.escape(section_title)}</span>')
    
    return f"""
    <div class="chunk-card" data-type="{element_type}" data-index="{idx}" data-search="{html.escape(text.lower())} {html.escape(section_title.lower())}">
        <div class="chunk-header">
            <span class="element-type-badge {badge_class}">{badge_text}</span>
            <span style="color: #a0aec0; font-size: 11px; font-family: monospace;">{html.escape(str(chunk_id)[:12])}</span>
        </div>
        <div class="chunk-meta">
            {" ".join(meta_items)}
        </div>
        <div class="chunk-text {direction}">{html.escape(text)}</div>
    </div>
    """


def _build_js(chunker_mode=False, comparison_mode=False) -> str:
    base_js = """
    <script>
    // Search functionality
    document.getElementById('searchBox').addEventListener('input', function(e) {
        const query = e.target.value.toLowerCase();
        const cards = document.querySelectorAll('[data-search]');
        
        cards.forEach(card => {
            const searchText = card.getAttribute('data-search');
            if (searchText.includes(query)) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    });
    
    // Filter by type
    let activeFilter = 'all';
    function filterByType(type) {
        activeFilter = type;
        const cards = document.querySelectorAll('[data-type]');
        const buttons = document.querySelectorAll('.filter-btn');
        
        buttons.forEach(btn => btn.classList.remove('active'));
        event.target.classList.add('active');
        
        cards.forEach(card => {
            if (type === 'all' || card.getAttribute('data-type') === type) {
                card.style.display = '';
            } else {
                card.style.display = 'none';
            }
        });
    }
    """
    
    if comparison_mode:
        base_js += """
        // Tab switching
        function showTab(tabId) {
            const tabs = document.querySelectorAll('.tab-content');
            const buttons = document.querySelectorAll('.tab-button');
            
            tabs.forEach(tab => tab.classList.remove('active'));
            buttons.forEach(btn => btn.classList.remove('active'));
            
            document.getElementById(tabId).classList.add('active');
            event.target.classList.add('active');
        }
        
        // Show first tab by default
        window.addEventListener('load', function() {
            const firstTab = document.querySelector('.tab-content');
            const firstButton = document.querySelector('.tab-button');
            if (firstTab && firstButton) {
                firstTab.classList.add('active');
                firstButton.classList.add('active');
            }
        });
        """
    
    base_js += "\n</script>"
    return base_js


def _compute_stats(elements: List[Dict]) -> Dict[str, Any]:
    if not elements:
        return {"total": 0}
    
    pages = set()
    headings = 0
    tables = 0
    images = 0
    text_lengths = []
    
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
            text_lengths.append(len(elem["text"]))
    
    return {
        "total": len(elements),
        "pages": len(pages),
        "headings": headings,
        "tables": tables,
        "images": images,
        "avg_text_length": int(sum(text_lengths) / len(text_lengths)) if text_lengths else 0,
    }


def _compute_chunk_stats(chunks: List[Dict]) -> Dict[str, Any]:
    if not chunks:
        return {"total": 0}
    
    token_counts = []
    sections = set()
    languages = set()
    
    for chunk in chunks:
        tc = chunk.get("token_count") or chunk.get("token_estimate", 0)
        if tc:
            token_counts.append(tc)
        
        if chunk.get("section_title"):
            sections.add(chunk["section_title"])
        
        if chunk.get("language"):
            languages.add(chunk["language"])
    
    return {
        "total": len(chunks),
        "avg_tokens": int(sum(token_counts) / len(token_counts)) if token_counts else 0,
        "min_tokens": min(token_counts) if token_counts else 0,
        "max_tokens": max(token_counts) if token_counts else 0,
        "sections": len(sections),
        "languages": ", ".join(sorted(languages)) if languages else "N/A",
    }


def _group_by_section(chunks: List[Dict]) -> OrderedDict:
    sections = OrderedDict()
    for chunk in chunks:
        section = chunk.get("section_title", "Untitled")
        if section not in sections:
            sections[section] = []
        sections[section].append(chunk)
    return sections


def _render_section_group(section_title: str, chunks: List[Dict]) -> str:
    html_parts = [f'<div class="section-group">']
    html_parts.append(f'<h3>{html.escape(section_title)} ({len(chunks)} chunks)</h3>')
    for idx, chunk in enumerate(chunks):
        html_parts.append(_render_chunk(chunk, idx))
    html_parts.append('</div>')
    return "".join(html_parts)

"""# 1. Visualize parser output
from ingestion.parsers.pdf_pymupdf import PyMuPDFParser
from ingestion.visualize import visualize_parser_output

parser = PyMuPDFParser()
with open("document.pdf", "rb") as f:
    elements = await parser.parse(f.read(), "document.pdf")

visualize_parser_output(
    elements,
    metadata={"parser": "PyMuPDF", "file": "document.pdf"},
    outpath="parser_output.html"
)

# 2. Visualize chunker output
from ingestion.chunkers.fixed import FixedChunker
from ingestion.visualize import visualize_chunker_output

chunker = FixedChunker()
chunks = chunker.chunk(normalized_elements)

visualize_chunker_output(
    chunks,
    metadata={"chunker": "Fixed", "chunk_size": 500},
    outpath="chunks.html"
)

# 3. Compare multiple methods
from ingestion.visualize import visualize_comparison

outputs = {
    "PyMuPDF": pymupdf_elements,
    "Docling": docling_elements,
    "PyMuPDF4LLM": pymupdf4llm_elements,
}

visualize_comparison(
    outputs,
    outpath="parser_comparison.html"
)

# 4. Compare chunkers
chunk_outputs = {
    "Fixed (500 tokens)": fixed_chunks,
    "Semantic": semantic_chunks,
    "Sentence": sentence_chunks,
}

visualize_comparison(
    chunk_outputs,
    outpath="chunker_comparison.html"
)"""