from io import BytesIO
from pathlib import Path

from docx import Document
from pypdf import PdfReader

SUPPORTED_EXTENSIONS = {".txt", ".pdf", ".docx"}


def detect_extension(filename: str) -> str:
    return Path(filename).suffix.lower()


def is_supported_article_file(filename: str) -> bool:
    return detect_extension(filename) in SUPPORTED_EXTENSIONS


def extract_text_from_txt(data: bytes) -> str:
    for encoding in ("utf-8", "utf-8-sig", "cp1251", "latin-1"):
        try:
            return data.decode(encoding).strip()
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="ignore").strip()


def extract_text_from_pdf(data: bytes) -> str:
    reader = PdfReader(BytesIO(data))
    parts: list[str] = []

    for page in reader.pages:
        text = page.extract_text() or ""
        text = text.strip()
        if text:
            parts.append(text)

    return "\n\n".join(parts).strip()


def extract_text_from_docx(data: bytes) -> str:
    doc = Document(BytesIO(data))
    parts: list[str] = []

    for paragraph in doc.paragraphs:
        text = paragraph.text.strip()
        if text:
            parts.append(text)

    return "\n\n".join(parts).strip()


def extract_text_from_file(filename: str, data: bytes) -> str:
    ext = detect_extension(filename)

    if ext == ".txt":
        return extract_text_from_txt(data)
    if ext == ".pdf":
        return extract_text_from_pdf(data)
    if ext == ".docx":
        return extract_text_from_docx(data)

    raise ValueError(f"Unsupported file type: {ext}")
