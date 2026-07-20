"""
Gemma OCR engine — last-resort OCR via Ollama Gemma2 27B.

Uses the Ollama HTTP API (not the Python SDK) to send a vision request with
the image encoded as base64.

The prompt is carefully crafted to:
- Preserve RTL text reading order (right-to-left for fa/ar)
- Maintain document structure (headings, tables, lists)
- Avoid hallucination ("only extract text, do not add commentary")

Config keys:
  OLLAMA_BASE_URL: str    — default "http://localhost:11434"
  GEMMA_OCR_MODEL: str    — default "gemma2:27b"
"""

from __future__ import annotations

import asyncio
import base64
from typing import Any

import structlog

from ingestion.ocr.base import BaseOCR, OCRError, OCRResult

logger = structlog.get_logger(__name__)

_SYSTEM_PROMPT = (
    "You are a precise OCR engine. "
    "Extract ALL text visible in the image. "
    "Preserve the original document structure: use Markdown headings for section titles, "
    "pipe tables for tabular data, and bullet lists for lists. "
    "For right-to-left text (Arabic, Persian, Hebrew, Urdu): preserve the original "
    "right-to-left reading order in your output. "
    "Do NOT add any commentary, translation, or explanation. "
    "Do NOT omit any text, no matter how small. "
    "Output only the extracted text in Markdown format."
)

_RTL_LANGUAGES = {"fa", "ar", "he", "ur", "prs", "ckb"}


class GemmaOCR(BaseOCR):
    """Last-resort OCR engine using Ollama Gemma2 27B."""

    @property
    def name(self) -> str:
        return "gemma_ocr"

    @property
    def available(self) -> bool:
        from ingestion.config import get_settings
        settings = get_settings()
        # Available if Ollama URL is set (even default localhost)
        return bool(settings.OLLAMA_BASE_URL)

    async def run(
        self,
        image_bytes: bytes,
        language_hint: str | None = None,
    ) -> OCRResult:
        from ingestion.config import get_settings

        try:
            import httpx
        except ImportError as exc:
            raise OCRError(self.name, "httpx not installed", exc)

        settings = get_settings()
        base_url = settings.OLLAMA_BASE_URL.rstrip("/")
        model = settings.GEMMA_OCR_MODEL

        # Build language-aware user prompt
        if language_hint and language_hint.lower() in _RTL_LANGUAGES:
            user_content = (
                f"Extract all text from this image. "
                f"The document is written in {language_hint} (right-to-left script). "
                f"Preserve the right-to-left reading order."
            )
        else:
            user_content = "Extract all text from this image."

        # Encode image
        b64 = base64.b64encode(image_bytes).decode("ascii")

        payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": user_content,
                    "images": [b64],
                },
            ],
            "stream": False,
            "options": {
                "temperature": 0.0,
                "num_predict": 4096,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=300.0) as client:
                response = await client.post(
                    f"{base_url}/api/chat",
                    json=payload,
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise OCRError(
                self.name,
                f"Ollama HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                exc,
            )
        except Exception as exc:
            raise OCRError(self.name, f"Ollama request failed: {exc}", exc)

        try:
            text = data["message"]["content"]
        except (KeyError, TypeError) as exc:
            raise OCRError(self.name, f"Unexpected Ollama response shape: {exc}", exc)

        if not text or not text.strip():
            raise OCRError(self.name, "Gemma returned empty text")

        return OCRResult(
            text=text.strip(),
            engine_name=self.name,
            confidence=None,  # Gemma doesn't produce confidence scores
            language_detected=language_hint,
            structured_elements=None,  # Raw Markdown; let normalizer handle it
        )
