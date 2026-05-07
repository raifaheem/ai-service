from app.services.article_parser import (
    _is_header,
    chunk_article_with_headers,
    normalize_article_text,
)


# --------------- normalize_article_text ---------------

def test_normalize_article_text():
    text = "Line 1\r\n\r\n\r\nLine 2   \t test"
    normalized = normalize_article_text(text)
    assert "\r" not in normalized
    assert "  " not in normalized
    assert "Line 1" in normalized
    assert "Line 2 test" in normalized


# --------------- _is_header ---------------

def test_is_header_markdown():
    assert _is_header("# Introduction") is True
    assert _is_header("## Methods") is True


def test_is_header_uppercase():
    assert _is_header("INTRODUCTION") is True
    assert _is_header("METHODS AND MATERIALS") is True


def test_is_header_colon():
    assert _is_header("Methods:") is True
    assert _is_header("Results and Discussion:") is True


def test_is_header_regular_text():
    assert _is_header("This is a regular sentence.") is False
    assert _is_header("") is False


# --------------- chunk_article_with_headers ---------------

def test_chunk_article_with_headers_basic():
    text = "# Introduction\n\nFirst paragraph about health.\n\n# Methods\n\nSecond paragraph about methods."
    chunks = chunk_article_with_headers(text, chunk_size=2000, overlap=200)

    assert len(chunks) >= 1
    # Check that headers are detected
    headers = [c["header"] for c in chunks]
    assert "# Introduction" in headers or "# Methods" in headers


def test_chunk_article_with_headers_prepends_header():
    text = "# Vitamins\n\nVitamin D is essential for bone health.\n\n# Minerals\n\nCalcium supports bone density."
    chunks = chunk_article_with_headers(text, chunk_size=2000, overlap=200)

    for chunk in chunks:
        if chunk["header"] == "# Vitamins":
            assert "# Vitamins" in chunk["text"]
            assert "Vitamin D" in chunk["text"]
        if chunk["header"] == "# Minerals":
            assert "# Minerals" in chunk["text"]
            assert "Calcium" in chunk["text"]


def test_chunk_article_with_headers_empty():
    assert chunk_article_with_headers("") == []


def test_chunk_article_with_headers_no_headers():
    text = "Just a paragraph.\n\nAnother paragraph."
    chunks = chunk_article_with_headers(text, chunk_size=2000, overlap=200)

    assert len(chunks) == 1
    assert chunks[0]["header"] is None
    assert "Just a paragraph" in chunks[0]["text"]


def test_chunk_article_with_headers_uppercase_header():
    text = "INTRODUCTION\n\nSome intro text.\n\nMETHODS\n\nSome methods text."
    chunks = chunk_article_with_headers(text, chunk_size=2000, overlap=200)

    headers = [c["header"] for c in chunks]
    assert "INTRODUCTION" in headers
    assert "METHODS" in headers
