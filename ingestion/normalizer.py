"""
Normalizer — converts list[ParsedElement] → list[dict] (the chunk contract).

Each output dict matches the Elasticsearch chunk schema exactly.
Language detection runs per-element via lingua-py.
RTL sorting is applied to bounding-box-based elements.
Section title is prepended to text to form section_title_text (for embedding).
"""

from __future__ import annotations

import uuid
from typing import Any, TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from ingestion.models import Document
    from ingestion.parsers.base import ParsedElement

logger = structlog.get_logger(__name__)

# Languages that use right-to-left script
_RTL_LANGUAGES = frozenset({"fa", "ar", "ur", "he", "prs", "ckb", "yi", "dv"})

# Map lingua Language codes to BCP-47 short codes
# lingua-py Language enum name → our language code
_LINGUA_MAP: dict[str, str] = {
    "ENGLISH": "en",
    "PERSIAN": "fa",
    "ARABIC": "ar",
    "URDU": "ur",
    "HEBREW": "he",
    "FRENCH": "fr",
    "GERMAN": "de",
    "SPANISH": "es",
    "TURKISH": "tr",
    "RUSSIAN": "ru",
    "CHINESE": "zh",
    "JAPANESE": "ja",
    "KOREAN": "ko",
}


# ---------------------------------------------------------------------------
# Language detection
# ---------------------------------------------------------------------------

def _build_detector():
    """
    Build a lingua LanguageDetector that covers all languages we care about.
    Cached at module level after first call.
    Returns None if lingua-language-detector is not installed.
    """
    try:
        from lingua import LanguageDetectorBuilder, Language  # type: ignore

        detector = (
            LanguageDetectorBuilder.from_all_languages()
            .with_minimum_relative_distance(0.1)
            .build()
        )
        return detector
    except ImportError:
        logger.warning("lingua_not_installed", fallback="no_language_detection")
        return None
    except Exception as exc:
        logger.warning("lingua_build_failed", error=str(exc))
        return None


_DETECTOR = None
_DETECTOR_INITIALIZED = False


def _get_detector():
    global _DETECTOR, _DETECTOR_INITIALIZED
    if not _DETECTOR_INITIALIZED:
        _DETECTOR = _build_detector()
        _DETECTOR_INITIALIZED = True
    return _DETECTOR


def _detect_language(
    text: str,
    confidence_threshold: float,
    hint: str | None = None,
) -> tuple[str, float | None]:
    """
    Detect language of `text`.
    Returns (language_code, confidence) or ("unknown", None).
    """
    if hint and hint in _LINGUA_MAP.values():
        return hint, 1.0

    detector = _get_detector()
    if detector is None:
        return hint or "unknown", None

    if len(text.split()) < 3:
        # Too short for reliable detection
        return hint or "unknown", None

    try:
        result = detector.compute_language_confidence_values(text)
        if not result:
            return "unknown", None

        # result is a list of (Language, confidence) sorted by confidence desc
        best = result[0]
        lang_enum_name = best.language.name  # e.g. "PERSIAN"
        confidence = best.value

        if confidence < confidence_threshold:
            return "unknown", confidence

        lang_code = _LINGUA_MAP.get(lang_enum_name, lang_enum_name.lower()[:2])
        return lang_code, confidence
    except Exception as exc:
        logger.warning("language_detection_failed", error=str(exc))
        return "unknown", None


# ---------------------------------------------------------------------------
# Bounding box heuristics
# ---------------------------------------------------------------------------

def _classify_by_bbox(
    element: "ParsedElement",
    page_height: float = 842.0,  # A4 default
) -> str:
    """
    Apply spatial heuristics to classify element type when no semantic label.
    Returns a refined element_type or the original one unchanged.
    """
    bb = element.bounding_box
    if bb is None:
        return element.element_type

    y0 = bb.get("y0", 0)
    y1 = bb.get("y1", 0)

    raw_meta = element.raw_metadata or {}
    font_size = raw_meta.get("font_size", 12.0)

    if font_size < 9 and y1 > page_height * 0.85:
        return "footnote"
    if y0 < page_height * 0.08:
        return element.element_type  # header region: keep original
    if y1 > page_height * 0.92:
        return "footnote"

    return element.element_type


# ---------------------------------------------------------------------------
# RTL bounding box sorting
# ---------------------------------------------------------------------------

def _sort_rtl_elements(elements: list["ParsedElement"]) -> list["ParsedElement"]:
    """
    For RTL pages, re-sort text blocks:
    - Group into y-bands (±20px)
    - Within each band, sort right-to-left (descending x0)
    """
    if not elements:
        return elements

    # Only sort elements that have bounding boxes
    with_bbox = [e for e in elements if e.bounding_box is not None]
    without_bbox = [e for e in elements if e.bounding_box is None]

    if not with_bbox:
        return elements

    # Sort by y first (top to bottom), then by x descending within band
    BAND_HEIGHT = 20.0

    def sort_key(el: "ParsedElement"):
        bb = el.bounding_box
        y_band = round(bb["y0"] / BAND_HEIGHT)
        x0 = bb["x0"]
        # Negate x0 so higher x0 comes first (right-to-left)
        return (y_band, -x0)

    sorted_with_bbox = sorted(with_bbox, key=sort_key)

    # Re-interleave with elements that had no bbox (preserve their relative order)
    # Strategy: place no-bbox elements at the end of their "block"
    return sorted_with_bbox + without_bbox


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    """
    Fast token estimate without loading a full tokenizer.
    Uses the rule-of-thumb: ~4 chars per token for Latin, ~2 for CJK/Arabic.
    """
    if not text:
        return 0
    # Simple heuristic: count whitespace-split words * 1.3 (sub-word factor)
    word_count = len(text.split())
    return max(1, int(word_count * 1.3))


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def normalize(
    elements: list["ParsedElement"],
    doc: "Document",
    confidence_threshold: float | None = None,
) -> list[dict]:
    """
    Convert a list of ParsedElement objects into the chunk contract dict format.

    Args:
        elements:             Output from any parser (or OCR router).
        doc:                  The Document SQL model instance (for doc_id, etc.).
        confidence_threshold: Minimum lingua confidence to assign a language code.
                              Defaults to settings.LANGUAGE_DETECTION_CONFIDENCE_THRESHOLD.

    Returns:
        list[dict] — each dict matches the Elasticsearch chunk schema.
    """
    from ingestion.config import get_settings

    settings = get_settings()
    if confidence_threshold is None:
        confidence_threshold = settings.LANGUAGE_DETECTION_CONFIDENCE_THRESHOLD

    ingestion_version = settings.INGESTION_VERSION

    # ---- Detect page height for bounding-box heuristics ----
    # Approximate from bounding boxes present; default A4
    page_heights: dict[int, float] = {}
    for el in elements:
        if el.bounding_box and el.page_number:
            y1 = el.bounding_box.get("y1", 0)
            pg = el.page_number
            if y1 > page_heights.get(pg, 0):
                page_heights[pg] = y1

    def get_page_height(page_num: int | None) -> float:
        if page_num and page_num in page_heights:
            return page_heights[page_num]
        return 842.0  # A4 default

    # ---- Detect overall document language for RTL detection ----
    # We check the first 500 chars of combined text
    combined_sample = " ".join(
        el.text[:100] for el in elements[:10] if el.text
    )
    doc_lang, _ = _detect_language(combined_sample, confidence_threshold)
    is_rtl_doc = doc_lang in _RTL_LANGUAGES

    # ---- If RTL doc, re-sort bbox elements by RTL reading order ----
    if is_rtl_doc:
        elements = _sort_rtl_elements(elements)

    # ---- Normalize each element ----
    chunks: list[dict] = []

    for el in elements:
        text = (el.text or "").strip()
        if not text and el.element_type not in ("image_page",):
            continue  # skip empty non-image elements

        # Language detection
        lang_code, lang_confidence = _detect_language(
            text, confidence_threshold, hint=el.language_hint
        )

        script_direction = "rtl" if lang_code in _RTL_LANGUAGES else "ltr"

        # Bounding-box element type refinement
        etype = _classify_by_bbox(el, get_page_height(el.page_number))

        # section_title_text = heading + text (for embedding)
        section_title = el.section_title or ""
        section_title_text = f"{section_title} {text}".strip() if section_title else text

        # Token estimate
        token_estimate = _estimate_tokens(text)

        chunk: dict[str, Any] = {
            # Identity
            "chunk_id": str(uuid.uuid4()),
            "doc_id": doc.id,
            # Content
            "text": text,
            "section_title_text": section_title_text,
            # Location
            "source_file": doc.original_filename,
            "doc_title": (doc.custom or {}).get("title", doc.original_filename),
            "page_number": el.page_number,
            "section_path": list(el.section_path or []),
            "section_title": section_title or None,
            # Type
            "element_type": etype,
            "is_table": el.is_table,
            "table_markdown": el.table_markdown,
            # Language
            "language": lang_code,
            "script_direction": script_direction,
            # Stats
            "chunk_index": len(chunks),
            "total_chunks": None,  # filled in after full list is built
            "char_count": len(text),
            "token_estimate": token_estimate,
            # Admin
            "tags": list(doc.tags or []),
            "ingestion_version": ingestion_version,
            # Vectors (filled in by vector_store.py)
            "dense_vector": None,
            "sparse_vector": None,
            # Heading metadata
            "heading_level": el.heading_level,
            # Debug
            "parser_name": el.parser_name,
            "bounding_box": el.bounding_box,
            "language_confidence": lang_confidence,
        }
        chunks.append(chunk)

    # ---- Fill in total_chunks now that we know the count ----
    total = len(chunks)
    for i, chunk in enumerate(chunks):
        chunk["total_chunks"] = total
        chunk["chunk_index"] = i

    logger.info(
        "normalization_complete",
        doc_id=doc.id,
        input_elements=len(elements),
        output_chunks=total,
        doc_language=doc_lang,
        is_rtl=is_rtl_doc,
    )
    return chunks
