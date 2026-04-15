import re
from typing import List


def normalize_article_text(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\n{3,}", "\n\n", text)
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def split_into_paragraphs(text: str) -> List[str]:
    normalized = normalize_article_text(text)
    paragraphs = [p.strip() for p in normalized.split("\n\n")]
    return [p for p in paragraphs if p]


def chunk_article_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 150,
) -> List[str]:
    normalized = normalize_article_text(text)

    if len(normalized) <= chunk_size:
        return [normalized]

    chunks: List[str] = []
    start = 0
    text_len = len(normalized)

    while start < text_len:
        end = min(start + chunk_size, text_len)
        chunk = normalized[start:end].strip()

        if chunk:
            chunks.append(chunk)

        if end >= text_len:
            break

        start = max(0, end - overlap)

    return chunks