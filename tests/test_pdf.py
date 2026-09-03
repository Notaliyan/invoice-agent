"""PDF extraction, end to end through a real generated PDF."""

from __future__ import annotations

import io

import pytest

from app.pdf import PdfDocument, PdfExtractionError, extract_pdf

reportlab = pytest.importorskip("reportlab", reason="reportlab is a dev dependency")


def _make_pdf(pages: list[list[str]]) -> bytes:
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfgen import canvas

    buffer = io.BytesIO()
    c = canvas.Canvas(buffer, pagesize=A4)
    for lines in pages:
        y = 800
        c.setFont("Helvetica", 11)
        for line in lines:
            c.drawString(50, y, line)
            y -= 16
        c.showPage()
    c.save()
    return buffer.getvalue()


def test_extracts_text_from_every_page():
    data = _make_pdf([["INVOICE 001", "Total: 100.00 EUR"], ["Page two content"]])
    doc = extract_pdf(data, "two-pager.pdf", max_pages=25)

    assert doc.page_count == 2
    assert "INVOICE 001" in doc.page_text(1)
    assert "Page two content" in doc.page_text(2)
    assert doc.char_count > 0


def test_lines_carry_page_and_line_numbers():
    data = _make_pdf([["alpha", "beta"], ["gamma"]])
    doc = extract_pdf(data, "x.pdf", max_pages=25)
    located = {(line.page, line.text) for line in doc.lines()}
    assert ("alpha" in [t for _, t in located]) or (1, "alpha") in located
    assert (2, "gamma") in located


def test_rejects_a_non_pdf():
    with pytest.raises(PdfExtractionError, match="%PDF"):
        extract_pdf(b"hello world", "notes.txt", max_pages=25)


def test_rejects_a_pdf_over_the_page_limit():
    data = _make_pdf([["a"], ["b"], ["c"]])
    with pytest.raises(PdfExtractionError, match="limit is 2"):
        extract_pdf(data, "long.pdf", max_pages=2)


def test_rejects_a_pdf_with_no_text():
    """An image-only PDF should fail loudly rather than reach the agent empty."""
    data = _make_pdf([[]])
    with pytest.raises(PdfExtractionError, match="scanned"):
        extract_pdf(data, "scan.pdf", max_pages=25)


def test_preview_marks_where_it_truncated():
    doc = PdfDocument.from_text("x" * 500)
    preview = doc.preview(100)
    assert preview.startswith("x" * 100)
    assert "truncated at 100 characters" in preview


def test_preview_leaves_short_pages_alone():
    doc = PdfDocument.from_text("short")
    assert doc.preview(100) == "short"
