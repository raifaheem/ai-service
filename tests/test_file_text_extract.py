"""Tests for app/services/file_text_extract.py (M10a).

Pre-M10 only TXT was covered (45% line coverage). This file adds in-memory
PDF + DOCX builds so the PDF / DOCX / dispatcher paths execute without
shipping fixture binaries in the repo.
"""

from io import BytesIO

import pytest
from docx import Document
from pypdf import PdfWriter

from app.services.file_text_extract import (
    detect_extension,
    extract_text_from_docx,
    extract_text_from_file,
    extract_text_from_pdf,
    extract_text_from_txt,
    is_supported_article_file,
)

# --------------- helpers ---------------


def _build_blank_pdf() -> bytes:
    """Minimum viable PDF: one blank page. Tests the empty-text path."""
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    buf = BytesIO()
    w.write(buf)
    return buf.getvalue()


def _build_docx(*paragraphs: str) -> bytes:
    """Real DOCX with the given paragraphs. python-docx serializes a real OOXML zip."""
    d = Document()
    for p in paragraphs:
        d.add_paragraph(p)
    buf = BytesIO()
    d.save(buf)
    return buf.getvalue()


# --------------- detect_extension / is_supported_article_file ---------------


def test_detect_extension():
    assert detect_extension("report.txt") == ".txt"
    assert detect_extension("paper.pdf") == ".pdf"
    assert detect_extension("notes.docx") == ".docx"
    assert detect_extension("article.PDF") == ".pdf"  # case-insensitive
    assert detect_extension("noext") == ""


def test_is_supported_article_file():
    assert is_supported_article_file("report.txt") is True
    assert is_supported_article_file("paper.pdf") is True
    assert is_supported_article_file("notes.docx") is True
    assert is_supported_article_file("image.png") is False


# --------------- extract_text_from_txt ---------------


def test_extract_text_from_txt_utf8():
    data = b"Migraine with nausea and photophobia"
    text = extract_text_from_txt(data)
    assert "Migraine" in text
    assert "photophobia" in text


def test_extract_text_from_txt_handles_invalid_bytes():
    """Bytes no codec accepts cleanly still return *something* (best-effort, no raise)."""
    extract_text_from_txt(b"\xff\xfe\xfd")  # must not raise


def test_extract_text_from_txt_cp1251_fallback():
    """A CP1251-encoded Russian string must decode via the fallback chain."""
    raw = "привет".encode("cp1251")
    out = extract_text_from_txt(raw)
    assert out  # non-empty (exact result depends on which codec wins)


# --------------- extract_text_from_pdf ---------------


def test_extract_text_from_pdf_blank_returns_empty_string():
    """A single-page PDF with no text content yields ''. The function still
    runs the page loop — covers the for-page path."""
    data = _build_blank_pdf()
    assert extract_text_from_pdf(data) == ""


def test_extract_text_from_pdf_raises_on_non_pdf():
    """pypdf raises on garbage input — articles router converts to HTTP 400."""
    with pytest.raises(Exception):  # noqa: B017 — pypdf raises various subclasses
        extract_text_from_pdf(b"not a pdf at all")


# --------------- extract_text_from_docx ---------------


def test_extract_text_from_docx_extracts_paragraphs():
    data = _build_docx("First paragraph.", "Second paragraph.")
    out = extract_text_from_docx(data)
    assert "First paragraph." in out
    assert "Second paragraph." in out
    # Joined with blank line.
    assert "\n\n" in out


def test_extract_text_from_docx_skips_blank_paragraphs():
    """Whitespace-only paragraphs must not produce empty entries in the join."""
    data = _build_docx("Real text.", "   ", "Another line.")
    out = extract_text_from_docx(data)
    parts = out.split("\n\n")
    assert "Real text." in parts
    assert "Another line." in parts
    # The blank/whitespace-only paragraph was dropped.
    assert all(p.strip() for p in parts)


# --------------- extract_text_from_file (dispatcher) ---------------


def test_dispatcher_routes_txt():
    assert extract_text_from_file("note.txt", b"hello") == "hello"


def test_dispatcher_routes_pdf():
    """Empty content is fine — what matters is that the .pdf branch ran."""
    data = _build_blank_pdf()
    assert extract_text_from_file("article.pdf", data) == ""


def test_dispatcher_routes_docx():
    data = _build_docx("docx content")
    assert "docx content" in extract_text_from_file("notes.docx", data)


def test_dispatcher_unsupported_extension_raises():
    with pytest.raises(ValueError, match="Unsupported file type"):
        extract_text_from_file("photo.jpeg", b"")
