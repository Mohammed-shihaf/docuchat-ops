"""Extract plain text from uploaded files entirely in memory - uploads are never written to disk."""
import io
from pathlib import PurePath

SUPPORTED = {".md", ".txt", ".pdf", ".docx"}


class UnsupportedFile(ValueError):
    pass


def extract_text(filename: str, data: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFile(f"unsupported file type '{ext or filename}'. Use: {', '.join(sorted(SUPPORTED))}")
    if ext in {".md", ".txt"}:
        return data.decode("utf-8", errors="replace")
    if ext == ".pdf":
        from pypdf import PdfReader
        reader = PdfReader(io.BytesIO(data))
        return "\n".join(page.extract_text() or "" for page in reader.pages)
    from docx import Document
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)
