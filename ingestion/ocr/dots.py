"""
dots.ocr engine — primary OCR via vLLM OpenAI-compatible endpoint.

Model: dots-community/dots-ocr-2.0
Deployment: vllm serve dots-community/dots-ocr-2.0

The engine sends the image as a base64-encoded vision request and parses the
Markdown response back into ParsedElement objects for structured output.

Config keys:
  DOTS_OCR_API_BASE: str  — vLLM endpoint URL, e.g. "http://gpu-server:8000/v1"
                           Empty string = engine is unavailable (skipped by router).
"""

from __future__ import annotations

import asyncio
import base64
import re
from typing import Any

import structlog

from ingestion.ocr.base import BaseOCR, OCRError, OCRResult

logger = structlog.get_logger(__name__)

# Markdown heading pattern
_MD_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)", re.MULTILINE)


def _markdown_to_elements(md_text: str) -> "list":
    """
    Parse a Markdown string from dots.ocr into a list of ParsedElement objects.
    Imports lazily to avoid circular imports at module load time.
    """
    from ingestion.parsers.base import ParsedElement

    elements: list[ParsedElement] = []
    lines = md_text.splitlines()
    section_headings: dict[int, str] = {}

    def current_path() -> list[str]:
        return [section_headings[lvl] for lvl in sorted(section_headings)]

    def current_title() -> str | None:
        if not section_headings:
            return None
        return section_headings[max(section_headings)]

    buffer: list[str] = []
    in_code_block = False

    def flush_buffer():
        text = "\n".join(buffer).strip()
        buffer.clear()
        if not text:
            return
        elements.append(
            ParsedElement(
                text=text,
                parser_name="dots_ocr",
                element_type="code" if in_code_block else "text",
                section_path=current_path(),
                section_title=current_title(),
                raw_metadata={"source": "dots_ocr"},
            )
        )

    for line in lines:
        # Code block toggle
        if line.startswith("```"):
            flush_buffer()
            in_code_block = not in_code_block
            continue

        if in_code_block:
            buffer.append(line)
            continue

        # Heading
        m = _MD_HEADING_RE.match(line)
        if m:
            flush_buffer()
            level = len(m.group(1))
            htext = m.group(2).strip()
            for lvl in range(level + 1, 7):
                section_headings.pop(lvl, None)
            section_headings[level] = htext
            elements.append(
                ParsedElement(
                    text=htext,
                    parser_name="dots_ocr",
                    element_type="heading",
                    heading_level=level,
                    section_path=current_path(),
                    section_title=htext,
                    raw_metadata={"source": "dots_ocr"},
                )
            )
            continue

        # Blank line → paragraph break
        if not line.strip():
            flush_buffer()
            continue

        buffer.append(line)

    flush_buffer()
    return elements


class DotsOCR(BaseOCR):
    """Primary OCR engine using dots-community/dots-ocr-2.0 via vLLM."""

    @property
    def name(self) -> str:
        return "dots_ocr"

    @property
    def available(self) -> bool:
        from ingestion.config import get_settings
        return bool(get_settings().DOTS_OCR_API_BASE)

    async def run(
        self,
        image_bytes: bytes,
        language_hint: str | None = None,
    ) -> OCRResult:
        from ingestion.config import get_settings

        settings = get_settings()
        base_url = settings.DOTS_OCR_API_BASE.rstrip("/")

        try:
            import httpx
        except ImportError as exc:
            raise OCRError(self.name, "httpx not installed", exc)

        # Encode image as base64 data URI
        b64 = base64.b64encode(image_bytes).decode("ascii")
        data_uri = f"data:image/png;base64,{b64}"

        payload = {
            "model": "dots-community/dots-ocr-2.0",
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": data_uri},
                        },
                        {
                            "type": "text",
                            "text": "prompt_layout_all_en",
                        },
                    ],
                }
            ],
            "temperature": 0.0,
            "max_tokens": 4096,
        }

        try:
            async with httpx.AsyncClient(timeout=120.0) as client:
                response = await client.post(
                    f"{base_url}/chat/completions",
                    json=payload,
                    headers={"Content-Type": "application/json"},
                )
                response.raise_for_status()
                data: dict[str, Any] = response.json()
        except httpx.HTTPStatusError as exc:
            raise OCRError(
                self.name,
                f"HTTP {exc.response.status_code}: {exc.response.text[:200]}",
                exc,
            )
        except Exception as exc:
            raise OCRError(self.name, f"Request failed: {exc}", exc)

        try:
            md_text = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OCRError(self.name, f"Unexpected response shape: {exc}", exc)

        structured = _markdown_to_elements(md_text)

        return OCRResult(
            text=md_text,
            engine_name=self.name,
            confidence=None,  # dots.ocr doesn't return confidence scores
            language_detected=language_hint,  # model is multilingual; trust hint
            structured_elements=structured if structured else None,
        )
