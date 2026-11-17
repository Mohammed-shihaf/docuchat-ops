"""Extract plain text from uploaded files entirely in memory - uploads are never written to disk.

Scanned PDFs and images go through OCR (Tesseract). pytesseract hands images to the tesseract
binary via /tmp, which is a RAM-only tmpfs in our containers, so OCR stays off disk too.
"""
import io
import shutil
from pathlib import PurePath

TEXT_TYPES = {".md", ".txt"}
IMAGE_TYPES = {".png", ".jpg", ".jpeg"}
SUPPORTED = TEXT_TYPES | IMAGE_TYPES | {".pdf", ".docx"}
MAX_OCR_PAGES = 30


class UnsupportedFile(ValueError):
    pass


def ocr_available() -> bool:
    return shutil.which("tesseract") is not None


def _ocr_image(image) -> str:
    import pytesseract
    return pytesseract.image_to_string(image)


def _pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    text = "\n".join(page.extract_text() or "" for page in PdfReader(io.BytesIO(data)).pages)
    if text.strip() or not ocr_available():
        return text
    # No text layer: it's a scanned PDF. Render each page in memory and OCR it.
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(data)
    pages = [pdf[i].render(scale=2).to_pil() for i in range(min(len(pdf), MAX_OCR_PAGES))]
    return "\n".join(_ocr_image(p) for p in pages)


def _docx_text(data: bytes) -> str:
    from docx import Document
    doc = Document(io.BytesIO(data))
    parts = [p.text for p in doc.paragraphs]
    for table in doc.tables:
        for row in table.rows:
            parts.append(" | ".join(cell.text for cell in row.cells))
    return "\n".join(parts)


def extract_text(filename: str, data: bytes) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise UnsupportedFile(f"unsupported file type '{ext or filename}'. Use: {', '.join(sorted(SUPPORTED))}")
    if ext in TEXT_TYPES:
        return data.decode("utf-8", errors="replace")
    if ext == ".pdf":
        return _pdf_text(data)
    if ext == ".docx":
        return _docx_text(data)
    if not ocr_available():
        raise UnsupportedFile("image OCR is not available on this server")
    from PIL import Image
    return _ocr_image(Image.open(io.BytesIO(data)))
