from app.services.article_parser import (
    chunk_article_text,
    normalize_article_text,
    chunk_article_with_headers,
    _is_header,
)


# --------------- normalize_article_text ---------------

def test_normalize_article_text():
    text = "Line 1\r\n\r\n\r\nLine 2   \t test"
    normalized = normalize_article_text(text)
    assert "\r" not in normalized
    assert "  " not in normalized
    assert "Line 1" in normalized
    assert "Line 2 test" in normalized


# --------------- chunk_article_text ---------------

def test_chunk_article_text_single_chunk():
    text = "A" * 300
    chunks = chunk_article_text(text, chunk_size=1000, overlap=150)
    assert len(chunks) == 1
    assert chunks[0] == text


def test_chunk_article_text_multiple_chunks():
    text = "A" * 2500
    chunks = chunk_article_text(text, chunk_size=1000, overlap=200)
    assert len(chunks) >= 3
    assert all(len(chunk) > 0 for chunk in chunks)


def test_chunk_article_text_empty():
    assert chunk_article_text("") == []
    assert chunk_article_text("   ") == []


def test_paragraph_aware_chunking():
    """Paragraphs should not be split mid-text when they fit within chunk_size."""
    para1 = "First paragraph about vitamins. " * 10  # ~320 chars
    para2 = "Second paragraph about minerals. " * 10  # ~330 chars
    para3 = "Third paragraph about exercise. " * 10  # ~320 chars
    text = f"{para1}\n\n{para2}\n\n{para3}"

    chunks = chunk_article_text(text, chunk_size=700, overlap=200)

    # Each paragraph is ~320-330 chars, chunk_size=700
    # So para1+para2 ~650 fits in one chunk, para3 in another
    assert len(chunks) >= 2

    # Verify paragraphs aren't split mid-sentence
    for chunk in chunks:
        # Each chunk should contain complete sentences (ending with period+space or period)
        if "First paragraph" in chunk:
            assert "vitamins." in chunk
        if "Third paragraph" in chunk:
            assert "exercise." in chunk


def test_chunk_overlap_exists():
    """Last paragraph of previous chunk should appear in next chunk (if within overlap)."""
    para1 = "A" * 400
    para2 = "B" * 150  # fits within overlap=200
    para3 = "C" * 400
    text = f"{para1}\n\n{para2}\n\n{para3}"

    chunks = chunk_article_text(text, chunk_size=600, overlap=200)

    # para2 is 150 chars, within overlap budget, so it should appear in two chunks
    assert len(chunks) >= 2
    chunks_with_b = [c for c in chunks if "B" * 50 in c]
    assert len(chunks_with_b) >= 2


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
