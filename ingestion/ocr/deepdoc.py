"""
DeepDoc OCR engine — local GPU/CPU fallback.

Uses deepdoc/vision/ from the RAGFlow project:
  - OCR class for text recognition
  - LayoutRecognizer for structural detection

If deepdoc is not installed at project root this engine marks itself
unavailable and the router skips it silently.

Config: no extra keys needed; uses local GPU/CPU automatically.
"""

from __future__ import annotations

import asyncio
import io
import sys
from pathlib import Path
from typing import Any

import structlog

from ingestion.ocr.base import BaseOCR, OCRError, OCRResult

logger = structlog.get_logger(__name__)

_DEEPDOC_AVAILABLE: bool | None = None


def _ensure_deepdoc_on_path() -> bool:
    global _DEEPDOC_AVAILABLE
    if _DEEPDOC_AVAILABLE is not None:
        return _DEEPDOC_AVAILABLE

    try:
        from deepdoc.vision import ocr as _  # type: ignore  # noqa: F401
        _DEEPDOC_AVAILABLE = True
        return True
    except ImportError:
        pass

    root = Path(__file__).resolve().parents[3]
    if (root / "deepdoc").is_dir() and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from deepdoc.vision import ocr as _  # type: ignore  # noqa: F401
        _DEEPDOC_AVAILABLE = True
    except ImportError:
        _DEEPDOC_AVAILABLE = False

    return _DEEPDOC_AVAILABLE  # type: ignore[return-value]


def _run_deepdoc_ocr(image_bytes: bytes) -> tuple[str, list]:
    """
    Synchronous deepdoc OCR run.  Returns (plain_text, layout_boxes).
    layout_boxes is a list of dicts with keys: text, bbox, type.
    """
    try:
        from deepdoc.vision.ocr import OCR  # type: ignore
    except ImportError as exc:
        raise OCRError("deepdoc_ocr", "deepdoc.vision.ocr not importable", exc)

    try:
        from PIL import Image  # type: ignore
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception as exc:
        raise OCRError("deepdoc_ocr", f"Cannot open image: {exc}", exc)

    try:
        ocr_model = OCR()
        boxes = ocr_model(img)
        # boxes: list of [bbox, text] or [bbox, (text, confidence)]
        texts: list[str] = []
        layout_boxes: list[dict] = []

        for item in boxes:
            bbox, content = item[0], item[1]
            if isinstance(content, (list, tuple)):
                text = content[0] if content else ""
                conf = float(content[1]) if len(content) > 1 else None
            else:
                text = str(content)
                conf = None

            text = (text or "").strip()
            if not text:
                continue
            texts.append(text)
            layout_boxes.append({"text": text, "bbox": bbox, "confidence": conf})

        return "\n".join(texts), layout_boxes
    except OCRError:
        raise
    except Exception as exc:
        raise OCRError("deepdoc_ocr", f"OCR inference failed: {exc}", exc)


class DeepDocOCR(BaseOCR):
    """Fallback OCR engine using deepdoc/vision/ (self-hosted)."""

    @property
    def name(self) -> str:
        return "deepdoc_ocr"

    @property
    def available(self) -> bool:
        return _ensure_deepdoc_on_path()

    async def run(
        self,
        image_bytes: bytes,
        language_hint: str | None = None,
    ) -> OCRResult:
        if not _ensure_deepdoc_on_path():
            raise OCRError(self.name, "deepdoc library not available")

        loop = asyncio.get_running_loop()
        try:
            text, layout_boxes = await loop.run_in_executor(
                None, _run_deepdoc_ocr, image_bytes
            )
        except OCRError:
            raise
        except Exception as exc:
            raise OCRError(self.name, f"Unexpected error: {exc}", exc)

        # Compute mean confidence if available
        confs = [b["confidence"] for b in layout_boxes if b.get("confidence") is not None]
        confidence = sum(confs) / len(confs) if confs else None

        # Build structured elements from layout boxes
        structured = None
        if layout_boxes:
            from ingestion.parsers.base import ParsedElement

            structured = [
                ParsedElement(
                    text=box["text"],
                    parser_name="deepdoc_ocr",
                    element_type="text",
                    bounding_box=(
                        {
                            "x0": box["bbox"][0][0],
                            "y0": box["bbox"][0][1],
                            "x1": box["bbox"][2][0],
                            "y1": box["bbox"][2][1],
                        }
                        if box.get("bbox")
                        else None
                    ),
                    raw_metadata={"confidence": box.get("confidence")},
                )
                for box in layout_boxes
                if box.get("text")
            ]

        return OCRResult(
            text=text,
            engine_name=self.name,
            confidence=confidence,
            language_detected=None,  # deepdoc doesn't expose language detection
            structured_elements=structured,
        )
