from app.services.article_parser import chunk_article_text, normalize_article_text


def test_normalize_article_text():
    text = "Line 1\r\n\r\n\r\nLine 2   \t test"
    normalized = normalize_article_text(text)
    assert "\r" not in normalized
    assert "  " not in normalized
    assert "Line 1" in normalized
    assert "Line 2 test" in normalized


def test_chunk_article_text_single_chunk():
    text = "A" * 300
    chunks = chunk_article_text(text, chunk_size=1000, overlap=150)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_article_text_multiple_chunks():
    text = "A" * 2500
    chunks = chunk_article_text(text, chunk_size=1000, overlap=150)
    assert len(chunks) >= 3
    assert all(len(chunk) > 0 for chunk in chunks)