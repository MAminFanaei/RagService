"""
OCR router — tries engines in priority order with quality validation.

Priority:
  1. DotsOCR      (vLLM endpoint, fastest, multilingual, structured output)
  2. DeepDocOCR   (local GPU/CPU, self-hosted)
  3. GemmaOCR     (Ollama Gemma2 27B, slowest but highest quality)

Quality validation rejects results that contain:
  - Repetition artifacts (same phrase repeated > 3 times)
  - Corruption (>30% non-alphanumeric / non-whitespace characters)
  - Empty output
"""

from __future__ import annotations

import re
import unicodedata

import structlog

from ingestion.ocr.base import AllOCRFailedError, OCRError, OCRResult

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------------------
# Quality validators
# ---------------------------------------------------------------------------

_FIVE_WORD_RE = re.compile(r"(\b\w+\b(?:\s+\b\w+\b){4})")


def _has_repetition(text: str, threshold: int = 3) -> bool:
    """
    Return True if any 5-word phrase appears more than `threshold` times.
    Guards against OCR hallucination / looping artifacts.
    """
    if len(text) < 20:
        return False
    matches = _FIVE_WORD_RE.findall(text.lower())
    phrase_counts: dict[str, int] = {}
    for phrase in matches:
        phrase_counts[phrase] = phrase_counts.get(phrase, 0) + 1
    return any(count > threshold for count in phrase_counts.values())


def _corruption_ratio(text: str) -> float:
    """
    Fraction of characters that are neither alphanumeric nor whitespace.
    High values indicate garbled output.
    """
    if not text:
        return 1.0
    bad = sum(
        1
        for ch in text
        if not (ch.isalnum() or ch.isspace() or unicodedata.category(ch).startswith("P"))
    )
    return bad / len(text)


def _is_valid_result(result: OCRResult) -> bool:
    """Return True if the OCR result passes quality checks."""
    text = (result.text or "").strip()
    if not text:
        logger.debug("ocr_quality_rejected", engine=result.engine_name, reason="empty")
        return False
    if _has_repetition(text):
        logger.warning("ocr_quality_rejected", engine=result.engine_name, reason="repetition")
        return False
    ratio = _corruption_ratio(text)
    if ratio > 0.30:
        logger.warning(
            "ocr_quality_rejected",
            engine=result.engine_name,
            reason="corruption",
            ratio=round(ratio, 3),
        )
        return False
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

async def route_and_ocr(
    image_bytes: bytes,
    language_hint: str | None = None,
) -> OCRResult:
    """
    Run OCR on `image_bytes`, trying engines in priority order until one
    returns a valid result.

    Args:
        image_bytes:   Raw image bytes (PNG or JPEG).
        language_hint: Optional BCP-47 language code ('fa', 'ar', 'en').

    Returns:
        First OCRResult that passes quality validation.

    Raises:
        AllOCRFailedError: When all engines fail or return invalid output.
    """
    from ingestion.ocr.dots import DotsOCR
    from ingestion.ocr.deepdoc import DeepDocOCR
    from ingestion.ocr.gemma import GemmaOCR

    engines = [DotsOCR(), DeepDocOCR(), GemmaOCR()]
    errors: list[str] = []

    for engine in engines:
        if not engine.available:
            logger.debug("ocr_engine_skipped", engine=engine.name, reason="not_available")
            continue

        try:
            log = logger.bind(engine=engine.name)
            log.info("ocr_attempting")
            result = await engine.run(image_bytes, language_hint)

            if _is_valid_result(result):
                log.info(
                    "ocr_succeeded",
                    text_length=len(result.text),
                    confidence=result.confidence,
                )
                return result
            else:
                errors.append(f"[{engine.name}] quality_rejected")

        except OCRError as exc:
            errors.append(str(exc))
            logger.warning(
                "ocr_engine_failed",
                engine=engine.name,
                reason=exc.reason,
            )
        except Exception as exc:
            errors.append(f"[{engine.name}] Unexpected: {exc}")
            logger.exception("ocr_engine_unexpected_error", engine=engine.name)

    raise AllOCRFailedError(errors=errors)
