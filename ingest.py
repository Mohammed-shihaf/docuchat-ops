"""Load every .md/.txt file in a folder into the vector store. Usage: python ingest.py docs/"""
import sys
from pathlib import Path

from app.rag import RagService


def main(folder: str) -> None:
    rag = RagService()
    files = [p for p in Path(folder).rglob("*") if p.suffix in {".md", ".txt"}]
    if not files:
        sys.exit(f"no .md/.txt files in {folder}")
    for path in files:
        n = rag.ingest(path.name, path.read_text(encoding="utf-8"))
        print(f"{path.name}: {n} chunks")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else "docs")
