from app.services.file_text_extract import (
    detect_extension,
    is_supported_article_file,
    extract_text_from_txt,
)


def test_detect_extension():
    assert detect_extension("report.txt") == ".txt"
    assert detect_extension("paper.pdf") == ".pdf"
    assert detect_extension("notes.docx") == ".docx"


def test_is_supported_article_file():
    assert is_supported_article_file("report.txt") is True
    assert is_supported_article_file("paper.pdf") is True
    assert is_supported_article_file("notes.docx") is True
    assert is_supported_article_file("image.png") is False


def test_extract_text_from_txt_utf8():
    data = "Migraine with nausea and photophobia".encode("utf-8")
    text = extract_text_from_txt(data)
    assert "Migraine" in text
    assert "photophobia" in text