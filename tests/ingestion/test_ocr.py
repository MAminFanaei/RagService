"""
Tests for Phase 2 OCR layer.

- OCRResult dataclass validation
- BaseOCR ABC enforcement
- Quality validators (repetition, corruption)
- Router fallback logic
- Individual engine availability/skip logic
- Gemma prompt construction
- dots.ocr Markdown parsing
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from ingestion.ocr.base import (
    BaseOCR,
    OCRError,
    OCRResult,
    AllOCRFailedError,
)
from ingestion.ocr.router import _has_repetition, _corruption_ratio, _is_valid_result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


# ---------------------------------------------------------------------------
# OCRResult
# ---------------------------------------------------------------------------

class TestOCRResult:
    def test_required_fields(self):
        r = OCRResult(text="hello", engine_name="test")
        assert r.text == "hello"
        assert r.engine_name == "test"

    def test_defaults(self):
        r = OCRResult(text="x", engine_name="e")
        assert r.confidence is None
        assert r.language_detected is None
        assert r.structured_elements is None

    def test_with_structured(self):
        from ingestion.parsers.base import ParsedElement

        el = ParsedElement(text="extracted", parser_name="ocr")
        r = OCRResult(
            text="extracted",
            engine_name="dots_ocr",
            confidence=0.95,
            language_detected="fa",
            structured_elements=[el],
        )
        assert len(r.structured_elements) == 1
        assert r.structured_elements[0].text == "extracted"


# ---------------------------------------------------------------------------
# BaseOCR ABC enforcement
# ---------------------------------------------------------------------------

class TestBaseOCRABC:
    def test_cannot_instantiate_abstract(self):
        with pytest.raises(TypeError):
            BaseOCR()

    def test_concrete_must_implement_run(self):
        class Incomplete(BaseOCR):
            @property
            def name(self):
                return "incomplete"
            # Missing: run()

        with pytest.raises(TypeError):
            Incomplete()

    def test_concrete_must_implement_name(self):
        class Incomplete(BaseOCR):
            async def run(self, image_bytes, language_hint=None):
                return OCRResult(text="", engine_name="x")
            # Missing: name property

        with pytest.raises(TypeError):
            Incomplete()

    def test_default_available_is_true(self):
        class ConcreteOCR(BaseOCR):
            @property
            def name(self):
                return "concrete"

            async def run(self, image_bytes, language_hint=None):
                return OCRResult(text="text", engine_name=self.name)

        ocr = ConcreteOCR()
        assert ocr.available is True


# ---------------------------------------------------------------------------
# OCRError
# ---------------------------------------------------------------------------

class TestOCRError:
    def test_message_format(self):
        err = OCRError("dots_ocr", "HTTP 500", ValueError("upstream error"))
        assert "dots_ocr" in str(err)
        assert "HTTP 500" in str(err)

    def test_without_cause(self):
        err = OCRError("gemma_ocr", "empty response")
        assert err.cause is None
        assert err.engine_name == "gemma_ocr"
        assert err.reason == "empty response"


# ---------------------------------------------------------------------------
# Quality validators
# ---------------------------------------------------------------------------

class TestQualityValidators:
    # Repetition detection
    def test_repetition_detected(self):
        phrase = "hello world this is a test"
        repeated = (phrase + " ") * 5
        assert _has_repetition(repeated) is True

    def test_no_repetition(self):
        text = "The quick brown fox jumps over the lazy dog. " * 2
        assert _has_repetition(text) is False

    def test_short_text_no_repetition(self):
        # Less than 20 chars always returns False
        assert _has_repetition("hello") is False

    def test_repetition_threshold(self):
        # Phrase repeated exactly 3 times → should NOT trigger (threshold > 3)
        phrase = "one two three four five"
        text = (phrase + " ") * 3
        assert _has_repetition(text, threshold=3) is False

    def test_repetition_above_threshold(self):
        phrase = "one two three four five"
        text = (phrase + " ") * 4
        assert _has_repetition(text, threshold=3) is True

    # Corruption ratio
    def test_clean_text_low_corruption(self):
        ratio = _corruption_ratio("Hello world, this is clean text.")
        assert ratio < 0.30

    def test_garbage_text_high_corruption(self):
        ratio = _corruption_ratio("�����▓▓▓░░░▒▒▒")
        # Non-alphanumeric non-whitespace
        assert ratio > 0.30

    def test_empty_text_max_corruption(self):
        assert _corruption_ratio("") == 1.0

    def test_rtl_text_not_corrupted(self):
        persian = "سلام دنیا این یک متن فارسی است"
        ratio = _corruption_ratio(persian)
        # Arabic/Persian chars are alphanumeric (isalnum() returns True)
        assert ratio < 0.30

    # is_valid_result
    def test_valid_result_passes(self):
        r = OCRResult(text="This is good clean text with enough content.", engine_name="test")
        assert _is_valid_result(r) is True

    def test_empty_result_fails(self):
        r = OCRResult(text="", engine_name="test")
        assert _is_valid_result(r) is False

    def test_whitespace_only_fails(self):
        r = OCRResult(text="   \n\t  ", engine_name="test")
        assert _is_valid_result(r) is False

    def test_repetitive_result_fails(self):
        phrase = "same phrase repeated over "
        r = OCRResult(text=phrase * 6, engine_name="test")
        assert _is_valid_result(r) is False

    def test_corrupted_result_fails(self):
        r = OCRResult(text="💀💀💀💀💀💀💀💀💀💀💀💀💀💀", engine_name="test")
        # High symbol ratio
        assert _is_valid_result(r) is False


# ---------------------------------------------------------------------------
# Router fallback
# ---------------------------------------------------------------------------

class TestOCRRouter:
    @pytest.mark.asyncio
    async def test_all_engines_fail_raises(self):
        from ingestion.ocr.router import route_and_ocr

        async def make_failing_engine(name):
            class FailEngine(BaseOCR):
                @property
                def name(self_inner):
                    return name

                async def run(self_inner, image_bytes, language_hint=None):
                    raise OCRError(name, "forced failure")

            return FailEngine()

        with patch("ingestion.ocr.router.DotsOCR") as MockDots, \
             patch("ingestion.ocr.router.DeepDocOCR") as MockDeep, \
             patch("ingestion.ocr.router.GemmaOCR") as MockGemma:

            for Mock, name in [(MockDots, "dots_ocr"), (MockDeep, "deepdoc_ocr"), (MockGemma, "gemma_ocr")]:
                instance = MagicMock()
                instance.name = name
                instance.available = True
                instance.run = AsyncMock(side_effect=OCRError(name, "forced failure"))
                Mock.return_value = instance

            with pytest.raises(AllOCRFailedError):
                await route_and_ocr(b"\x89PNG\r\n\x1a\n")

    @pytest.mark.asyncio
    async def test_first_valid_engine_used(self):
        from ingestion.ocr.router import route_and_ocr

        with patch("ingestion.ocr.router.DotsOCR") as MockDots, \
             patch("ingestion.ocr.router.DeepDocOCR") as MockDeep, \
             patch("ingestion.ocr.router.GemmaOCR") as MockGemma:

            good_result = OCRResult(
                text="Valid clean OCR result from the first engine.",
                engine_name="dots_ocr",
            )
            dots_instance = MagicMock()
            dots_instance.name = "dots_ocr"
            dots_instance.available = True
            dots_instance.run = AsyncMock(return_value=good_result)
            MockDots.return_value = dots_instance

            # Second and third engines should NOT be called
            deep_instance = MagicMock()
            deep_instance.name = "deepdoc_ocr"
            deep_instance.available = True
            deep_instance.run = AsyncMock(side_effect=AssertionError("should not be called"))
            MockDeep.return_value = deep_instance

            gemma_instance = MagicMock()
            gemma_instance.name = "gemma_ocr"
            gemma_instance.available = True
            gemma_instance.run = AsyncMock(side_effect=AssertionError("should not be called"))
            MockGemma.return_value = gemma_instance

            result = await route_and_ocr(b"\x89PNG", "fa")
            assert result.engine_name == "dots_ocr"
            assert "Valid" in result.text

    @pytest.mark.asyncio
    async def test_unavailable_engines_skipped(self):
        from ingestion.ocr.router import route_and_ocr

        with patch("ingestion.ocr.router.DotsOCR") as MockDots, \
             patch("ingestion.ocr.router.DeepDocOCR") as MockDeep, \
             patch("ingestion.ocr.router.GemmaOCR") as MockGemma:

            # dots and deepdoc unavailable
            for Mock, name in [(MockDots, "dots_ocr"), (MockDeep, "deepdoc_ocr")]:
                instance = MagicMock()
                instance.name = name
                instance.available = False
                instance.run = AsyncMock(side_effect=AssertionError("should not be called"))
                Mock.return_value = instance

            # gemma is available and succeeds
            gemma_result = OCRResult(
                text="Gemma extracted this text from the document page.",
                engine_name="gemma_ocr",
            )
            gemma_instance = MagicMock()
            gemma_instance.name = "gemma_ocr"
            gemma_instance.available = True
            gemma_instance.run = AsyncMock(return_value=gemma_result)
            MockGemma.return_value = gemma_instance

            result = await route_and_ocr(b"\x89PNG")
            assert result.engine_name == "gemma_ocr"

    @pytest.mark.asyncio
    async def test_quality_rejected_falls_through(self):
        from ingestion.ocr.router import route_and_ocr

        with patch("ingestion.ocr.router.DotsOCR") as MockDots, \
             patch("ingestion.ocr.router.DeepDocOCR") as MockDeep, \
             patch("ingestion.ocr.router.GemmaOCR") as MockGemma:

            # First engine: returns garbage (quality fails)
            bad_phrase = "bad text repeated over "
            bad_result = OCRResult(text=bad_phrase * 6, engine_name="dots_ocr")
            dots_instance = MagicMock()
            dots_instance.name = "dots_ocr"
            dots_instance.available = True
            dots_instance.run = AsyncMock(return_value=bad_result)
            MockDots.return_value = dots_instance

            # Second engine fails with OCRError
            deep_instance = MagicMock()
            deep_instance.name = "deepdoc_ocr"
            deep_instance.available = True
            deep_instance.run = AsyncMock(side_effect=OCRError("deepdoc_ocr", "inference failed"))
            MockDeep.return_value = deep_instance

            # Third engine succeeds
            good_result = OCRResult(
                text="Good clean text from Gemma OCR engine here.",
                engine_name="gemma_ocr",
            )
            gemma_instance = MagicMock()
            gemma_instance.name = "gemma_ocr"
            gemma_instance.available = True
            gemma_instance.run = AsyncMock(return_value=good_result)
            MockGemma.return_value = gemma_instance

            result = await route_and_ocr(b"\x89PNG")
            assert result.engine_name == "gemma_ocr"


# ---------------------------------------------------------------------------
# dots.ocr Markdown parsing
# ---------------------------------------------------------------------------

class TestDotsMarkdownParsing:
    def test_headings_parsed(self):
        from ingestion.ocr.dots import _markdown_to_elements

        md = "# Section 1\n\nSome body text here.\n\n## Section 1.1\n\nMore text."
        elements = _markdown_to_elements(md)
        headings = [e for e in elements if e.element_type == "heading"]
        assert len(headings) == 2
        assert headings[0].heading_level == 1
        assert headings[1].heading_level == 2

    def test_body_text_extracted(self):
        from ingestion.ocr.dots import _markdown_to_elements

        md = "# Title\n\nBody paragraph here.\n\nAnother paragraph."
        elements = _markdown_to_elements(md)
        texts = [e for e in elements if e.element_type == "text"]
        assert len(texts) >= 1

    def test_section_path_propagated(self):
        from ingestion.ocr.dots import _markdown_to_elements

        md = "# Chapter\n\nText under chapter."
        elements = _markdown_to_elements(md)
        body = [e for e in elements if e.element_type == "text"]
        assert len(body) > 0
        assert body[0].section_path == ["Chapter"]

    def test_empty_markdown_returns_empty_list(self):
        from ingestion.ocr.dots import _markdown_to_elements

        elements = _markdown_to_elements("")
        assert elements == []

    def test_code_block_detected(self):
        from ingestion.ocr.dots import _markdown_to_elements

        md = "Some text.\n\n```\ncode here\n```\n\nMore text."
        elements = _markdown_to_elements(md)
        code_elements = [e for e in elements if e.element_type == "code"]
        assert len(code_elements) >= 1


# ---------------------------------------------------------------------------
# DotsOCR engine
# ---------------------------------------------------------------------------

class TestDotsOCR:
    def test_name(self):
        from ingestion.ocr.dots import DotsOCR
        assert DotsOCR().name == "dots_ocr"

    def test_unavailable_when_no_api_base(self):
        from ingestion.ocr.dots import DotsOCR
        with patch("ingestion.ocr.dots.get_settings") as mock_settings:
            mock_settings.return_value.DOTS_OCR_API_BASE = ""
            engine = DotsOCR()
            assert engine.available is False

    def test_available_when_api_base_set(self):
        from ingestion.ocr.dots import DotsOCR
        with patch("ingestion.ocr.dots.get_settings") as mock_settings:
            mock_settings.return_value.DOTS_OCR_API_BASE = "http://gpu:8000/v1"
            engine = DotsOCR()
            assert engine.available is True

    @pytest.mark.asyncio
    async def test_http_error_raises_ocr_error(self):
        from ingestion.ocr.dots import DotsOCR
        import httpx

        engine = DotsOCR()
        with patch("ingestion.ocr.dots.get_settings") as mock_settings:
            mock_settings.return_value.DOTS_OCR_API_BASE = "http://gpu:8000/v1"
            with patch("httpx.AsyncClient") as MockClient:
                mock_resp = MagicMock()
                mock_resp.status_code = 500
                mock_resp.text = "Internal Server Error"
                MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                    side_effect=httpx.HTTPStatusError(
                        "500", request=MagicMock(), response=mock_resp
                    )
                )
                with pytest.raises(OCRError) as exc_info:
                    await engine.run(b"\x89PNG", "en")
                assert "500" in str(exc_info.value)


# ---------------------------------------------------------------------------
# DeepDocOCR engine
# ---------------------------------------------------------------------------

class TestDeepDocOCR:
    def test_name(self):
        from ingestion.ocr.deepdoc import DeepDocOCR
        assert DeepDocOCR().name == "deepdoc_ocr"

    @pytest.mark.asyncio
    async def test_unavailable_raises_ocr_error(self):
        from ingestion.ocr.deepdoc import DeepDocOCR

        engine = DeepDocOCR()
        with patch("ingestion.ocr.deepdoc._ensure_deepdoc_on_path", return_value=False):
            with pytest.raises(OCRError) as exc_info:
                await engine.run(b"\x89PNG")
            assert "not available" in str(exc_info.value).lower()


# ---------------------------------------------------------------------------
# GemmaOCR engine
# ---------------------------------------------------------------------------

class TestGemmaOCR:
    def test_name(self):
        from ingestion.ocr.gemma import GemmaOCR
        assert GemmaOCR().name == "gemma_ocr"

    def test_available_when_ollama_url_set(self):
        from ingestion.ocr.gemma import GemmaOCR
        with patch("ingestion.ocr.gemma.get_settings") as mock_settings:
            mock_settings.return_value.OLLAMA_BASE_URL = "http://localhost:11434"
            engine = GemmaOCR()
            assert engine.available is True

    def test_rtl_prompt_for_persian(self):
        from ingestion.ocr.gemma import _RTL_LANGUAGES
        assert "fa" in _RTL_LANGUAGES
        assert "ar" in _RTL_LANGUAGES
        assert "he" in _RTL_LANGUAGES
        assert "en" not in _RTL_LANGUAGES

    @pytest.mark.asyncio
    async def test_empty_response_raises_ocr_error(self):
        from ingestion.ocr.gemma import GemmaOCR
        import httpx

        engine = GemmaOCR()
        with patch("ingestion.ocr.gemma.get_settings") as mock_settings:
            mock_settings.return_value.OLLAMA_BASE_URL = "http://localhost:11434"
            mock_settings.return_value.GEMMA_OCR_MODEL = "gemma2:27b"
            with patch("httpx.AsyncClient") as MockClient:
                MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=MagicMock(
                        raise_for_status=MagicMock(),
                        json=MagicMock(return_value={"message": {"content": "   "}}),
                    )
                )
                with pytest.raises(OCRError) as exc_info:
                    await engine.run(b"\x89PNG", "en")
                assert "empty" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_successful_response(self):
        from ingestion.ocr.gemma import GemmaOCR

        engine = GemmaOCR()
        with patch("ingestion.ocr.gemma.get_settings") as mock_settings:
            mock_settings.return_value.OLLAMA_BASE_URL = "http://localhost:11434"
            mock_settings.return_value.GEMMA_OCR_MODEL = "gemma2:27b"
            with patch("httpx.AsyncClient") as MockClient:
                MockClient.return_value.__aenter__.return_value.post = AsyncMock(
                    return_value=MagicMock(
                        raise_for_status=MagicMock(),
                        json=MagicMock(
                            return_value={"message": {"content": "Extracted text here."}}
                        ),
                    )
                )
                result = await engine.run(b"\x89PNG", "en")
                assert result.text == "Extracted text here."
                assert result.engine_name == "gemma_ocr"


# ---------------------------------------------------------------------------
# AllOCRFailedError
# ---------------------------------------------------------------------------

class TestAllOCRFailedError:
    def test_message_contains_errors(self):
        err = AllOCRFailedError(errors=["[dots_ocr] failed", "[gemma_ocr] timed out"])
        assert "dots_ocr" in str(err)
        assert "gemma_ocr" in str(err)
        assert len(err.errors) == 2
