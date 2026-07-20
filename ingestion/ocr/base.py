"""
Base types for all OCR engines.

OCRResult is the unified output type.  Every engine must return this.
BaseOCR is the abstract base class every engine implements.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ingestion.parsers.base import ParsedElement


@dataclass
class OCRResult:
    """
    Unified output from every OCR engine.

    `structured_elements` is populated when the engine returns layout-aware
    output (e.g., dots.ocr Markdown → parsed back into ParsedElement list).
    When None, the router emits a single ParsedElement from `text`.
    """

    text: str
    engine_name: str
    confidence: float | None = None
    language_detected: str | None = None
    structured_elements: "list[ParsedElement] | None" = None


class BaseOCR(ABC):
    """
    Abstract base for all OCR engines.

    Methods are async so they can be called from async Celery task wrappers,
    even if the underlying engine is synchronous (use run_in_executor).
    """

    @abstractmethod
    async def run(
        self,
        image_bytes: bytes,
        language_hint: str | None = None,
    ) -> OCRResult:
        """
        Run OCR on a single image.

        Args:
            image_bytes:   Raw image bytes (PNG preferred; JPEG acceptable).
            language_hint: BCP-47 language code hint ('fa', 'ar', 'en', ...).
                           Engines that support hints will use it; others ignore it.

        Returns:
            OCRResult with at minimum `text` and `engine_name` populated.

        Raises:
            OCRError: On hard failure (router will try next engine).
        """
        ...

    @property
    @abstractmethod
    def name(self) -> str:
        """Short engine identifier used in logging and Document.ocr_used."""
        ...

    @property
    def available(self) -> bool:
        """
        Return False if the engine is not configured/reachable.
        The router skips unavailable engines without attempting a call.
        """
        return True


class OCRError(Exception):
    """Raised by an OCR engine to signal the router should try the next one."""

    def __init__(self, engine_name: str, reason: str, cause: Exception | None = None):
        self.engine_name = engine_name
        self.reason = reason
        self.cause = cause
        super().__init__(
            f"[{engine_name}] {reason}" + (f": {cause}" if cause else "")
        )


class AllOCRFailedError(Exception):
    """Raised when every OCR engine in the fallback chain has failed."""

    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__("All OCR engines failed. Errors: " + "; ".join(errors))
