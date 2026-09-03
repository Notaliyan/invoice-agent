"""PDF text extraction.

Kept deliberately thin: pdfplumber gives us per-page text, and everything
downstream works on plain strings. Scanned/image-only PDFs are rejected here
rather than silently handed to the agent as an empty document.
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass


class PdfExtractionError(Exception):
    """Raised when a file cannot be turned into usable text."""


@dataclass(frozen=True)
class Line:
    page: int  # 1-indexed
    number: int  # 1-indexed within the page
    text: str


@dataclass
class PdfDocument:
    filename: str
    pages: list[str]

    @property
    def page_count(self) -> int:
        return len(self.pages)

    @property
    def text(self) -> str:
        return "\n".join(self.pages)

    @property
    def char_count(self) -> int:
        return sum(len(p) for p in self.pages)

    def page_text(self, page: int) -> str:
        if not 1 <= page <= self.page_count:
            raise IndexError(f"page {page} out of range (document has {self.page_count})")
        return self.pages[page - 1]

    def lines(self) -> list[Line]:
        out: list[Line] = []
        for p_idx, page in enumerate(self.pages, start=1):
            for l_idx, raw in enumerate(page.splitlines(), start=1):
                text = raw.strip()
                if text:
                    out.append(Line(page=p_idx, number=l_idx, text=text))
        return out

    def preview(self, chars: int) -> str:
        head = self.pages[0] if self.pages else ""
        if len(head) <= chars:
            return head
        return head[:chars] + f"\n... [page 1 truncated at {chars} characters]"

    @classmethod
    def from_text(cls, text: str, filename: str = "inline.txt") -> PdfDocument:
        """Escape hatch for tests and the /process/text endpoint."""
        return cls(filename=filename, pages=[text])


_WHITESPACE = re.compile(r"[ \t ]+")


def _tidy(text: str) -> str:
    """Collapse the ragged spacing pdfplumber leaves behind on table-ish PDFs."""
    lines = [_WHITESPACE.sub(" ", ln).rstrip() for ln in text.splitlines()]
    return "\n".join(lines).strip()


def extract_pdf(data: bytes, filename: str, max_pages: int) -> PdfDocument:
    if not data.startswith(b"%PDF"):
        raise PdfExtractionError("File does not look like a PDF (missing %PDF header).")

    import pdfplumber  # imported lazily; it pulls in pdfminer and is slow to load

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            if len(pdf.pages) > max_pages:
                raise PdfExtractionError(
                    f"PDF has {len(pdf.pages)} pages, limit is {max_pages}."
                )
            pages = [_tidy(page.extract_text() or "") for page in pdf.pages]
    except PdfExtractionError:
        raise
    except Exception as exc:  # pdfminer raises a wide variety of things
        raise PdfExtractionError(f"Could not parse PDF: {exc}") from exc

    if not any(pages):
        raise PdfExtractionError(
            "No extractable text found. This is probably a scanned image; "
            "OCR is not supported yet."
        )

    return PdfDocument(filename=filename, pages=pages)
