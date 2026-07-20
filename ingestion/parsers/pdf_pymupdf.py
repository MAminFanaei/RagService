# ingestion/parsers/pdf_pymupdf.py
"""PyMuPDF parser - no pymupdf4llm dependency needed."""

import fitz  # PyMuPDF
from ingestion.parsers.base import BaseParser, ParsedElement


class PyMuPDFParser(BaseParser):
    """
    Fast PDF parser using only PyMuPDF.
    Works without pymupdf4llm.
    """

    @property
    def name(self) -> str:
        return "pymupdf"

    async def parse(self, data: bytes, filename: str) -> list[ParsedElement]:
        doc = fitz.open(stream=data, filetype="pdf")
        elements = []

        for page_num, page in enumerate(doc, start=1):
            # Extract text with structure
            text = page.get_text("text")
            blocks = page.get_text("blocks")
            images = page.get_images()

            # Detect scanned pages
            if len(text.strip()) < 50 and len(images) > 0:
                # Return image for OCR
                pix = page.get_pixmap(dpi=200)
                elements.append(ParsedElement(
                    text="",
                    element_type="image_page",
                    page_number=page_num,
                    bounding_box=None,
                    section_title=None,
                    section_path=[],
                    heading_level=None,
                    is_table=False,
                    table_markdown=None,
                    language_hint=None,
                    parser_name=self.name,
                    raw_metadata={
                        "image_bytes": pix.tobytes("png"),
                        "needs_ocr": True,
                    },
                ))
                continue

            # Extract text blocks
            for block in blocks:
                if block[6] == 0:  # text block
                    block_text = block[4].strip()
                    if block_text:
                        elements.append(ParsedElement(
                            text=block_text,
                            element_type="text",
                            page_number=page_num,
                            bounding_box={
                                "x0": block[0],
                                "y0": block[1],
                                "x1": block[2],
                                "y1": block[3],
                            },
                            section_title=None,
                            section_path=[],
                            heading_level=None,
                            is_table=False,
                            table_markdown=None,
                            language_hint=None,
                            parser_name=self.name,
                            raw_metadata={"block_no": block[5]},
                        ))

        doc.close()
        return elements