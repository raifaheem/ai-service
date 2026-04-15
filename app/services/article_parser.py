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


def _is_header(line: str) -> bool:
    stripped = line.strip()
    if not stripped:
        return False
    if stripped.startswith("#"):
        return True
    if stripped.isupper() and len(stripped) > 3:
        return True
    if stripped.endswith(":") and len(stripped) < 100 and "\n" not in stripped:
        return True
    return False


def chunk_article_text(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> List[str]:
    normalized = normalize_article_text(text)

    if len(normalized) <= chunk_size:
        return [normalized] if normalized else []

    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]

    chunks: List[str] = []
    current_parts: List[str] = []
    current_len = 0

    for para in paragraphs:
        para_len = len(para)

        # If single paragraph exceeds chunk_size, split it by characters
        if para_len > chunk_size:
            # Flush current buffer first
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_len = 0

            # Character-level splitting for oversized paragraph
            start = 0
            while start < para_len:
                end = min(start + chunk_size, para_len)
                chunk = para[start:end].strip()
                if chunk:
                    chunks.append(chunk)
                if end >= para_len:
                    break
                start = max(0, end - overlap)
            continue

        # Would adding this paragraph exceed chunk_size?
        separator_len = 2 if current_parts else 0  # "\n\n" between paragraphs
        if current_len + separator_len + para_len > chunk_size and current_parts:
            chunks.append("\n\n".join(current_parts))

            # Overlap: keep the last paragraph if it fits within overlap budget
            last_part = current_parts[-1]
            if len(last_part) <= overlap:
                current_parts = [last_part]
                current_len = len(last_part)
            else:
                current_parts = []
                current_len = 0

        current_parts.append(para)
        current_len += separator_len + para_len

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    return chunks


def chunk_article_with_headers(
    text: str,
    chunk_size: int = 1000,
    overlap: int = 200,
) -> List[dict]:
    normalized = normalize_article_text(text)

    if not normalized:
        return []

    paragraphs = [p.strip() for p in normalized.split("\n\n") if p.strip()]

    chunks: List[dict] = []
    current_parts: List[str] = []
    current_len = 0
    current_header: str | None = None

    for para in paragraphs:
        # Check if this paragraph is a header
        lines = para.split("\n")
        if len(lines) == 1 and _is_header(lines[0]):
            # Flush current buffer
            if current_parts:
                chunk_text = "\n\n".join(current_parts)
                if current_header:
                    chunk_text = f"{current_header}\n\n{chunk_text}"
                chunks.append({"text": chunk_text, "header": current_header})
                current_parts = []
                current_len = 0
            current_header = lines[0].strip()
            continue

        para_len = len(para)

        # Oversized paragraph
        if para_len > chunk_size:
            if current_parts:
                chunk_text = "\n\n".join(current_parts)
                if current_header:
                    chunk_text = f"{current_header}\n\n{chunk_text}"
                chunks.append({"text": chunk_text, "header": current_header})
                current_parts = []
                current_len = 0

            start = 0
            while start < para_len:
                end = min(start + chunk_size, para_len)
                piece = para[start:end].strip()
                if piece:
                    full_text = f"{current_header}\n\n{piece}" if current_header else piece
                    chunks.append({"text": full_text, "header": current_header})
                if end >= para_len:
                    break
                start = max(0, end - overlap)
            continue

        separator_len = 2 if current_parts else 0
        if current_len + separator_len + para_len > chunk_size and current_parts:
            chunk_text = "\n\n".join(current_parts)
            if current_header:
                chunk_text = f"{current_header}\n\n{chunk_text}"
            chunks.append({"text": chunk_text, "header": current_header})

            last_part = current_parts[-1]
            if len(last_part) <= overlap:
                current_parts = [last_part]
                current_len = len(last_part)
            else:
                current_parts = []
                current_len = 0

        current_parts.append(para)
        current_len += separator_len + para_len

    if current_parts:
        chunk_text = "\n\n".join(current_parts)
        if current_header:
            chunk_text = f"{current_header}\n\n{chunk_text}"
        chunks.append({"text": chunk_text, "header": current_header})

    return chunks
