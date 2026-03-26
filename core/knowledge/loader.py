"""
Aleph Framework — Knowledge File Loaders
==========================================
Reads files and extracts text content for chunking.
Supports: PDF, Markdown, TXT, CSV.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass
from io import StringIO
from pathlib import Path

logger = logging.getLogger("aleph.knowledge")


@dataclass
class LoadedDocument:
    """A loaded document ready for chunking."""
    source: str          # filename
    content: str         # full text content
    metadata: dict       # extra info (pages, format, etc)


def load_file(path: Path) -> LoadedDocument:
    """Load a single file and extract text content.

    Args:
        path: Path to the file

    Returns:
        LoadedDocument with extracted text

    Raises:
        ValueError: If file format is not supported
        FileNotFoundError: If file doesn't exist
    """
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    suffix = path.suffix.lower()
    source = path.name

    if suffix == ".pdf":
        return _load_pdf(path, source)
    elif suffix in (".md", ".txt", ".text"):
        return _load_text(path, source)
    elif suffix == ".csv":
        return _load_csv(path, source)
    else:
        raise ValueError(
            f"Unsupported file format: {suffix}\n"
            f"Supported: .pdf, .md, .txt, .csv"
        )


def load_directory(dir_path: Path) -> list[LoadedDocument]:
    """Load all supported files from a directory.

    Args:
        dir_path: Path to the directory

    Returns:
        List of LoadedDocument objects
    """
    if not dir_path.is_dir():
        raise FileNotFoundError(f"Directory not found: {dir_path}")

    supported = {".pdf", ".md", ".txt", ".text", ".csv"}
    docs = []

    for path in sorted(dir_path.iterdir()):
        if path.suffix.lower() in supported:
            try:
                doc = load_file(path)
                docs.append(doc)
                logger.info("Loaded: %s (%d chars)", doc.source, len(doc.content))
            except Exception as e:
                logger.warning("Failed to load %s: %s", path.name, e)

    return docs


# ---------------------------------------------------------------------------
# Format-specific loaders
# ---------------------------------------------------------------------------

def _load_pdf(path: Path, source: str) -> LoadedDocument:
    """Load a PDF file using pypdf."""
    try:
        from pypdf import PdfReader
    except ImportError:
        raise RuntimeError(
            "pypdf is required for PDF loading. "
            "Install with: pip install pypdf"
        )

    reader = PdfReader(str(path))
    pages = []
    for i, page in enumerate(reader.pages):
        text = page.extract_text()
        if text and text.strip():
            pages.append(text.strip())

    content = "\n\n".join(pages)

    logger.debug("PDF loaded: %s (%d pages, %d chars)", source, len(reader.pages), len(content))

    return LoadedDocument(
        source=source,
        content=content,
        metadata={"format": "pdf", "pages": len(reader.pages)},
    )


def _load_text(path: Path, source: str) -> LoadedDocument:
    """Load a text/markdown file."""
    content = path.read_text(encoding="utf-8").strip()

    return LoadedDocument(
        source=source,
        content=content,
        metadata={"format": path.suffix.lstrip(".")},
    )


def _load_csv(path: Path, source: str) -> LoadedDocument:
    """Load a CSV file, converting rows to readable text."""
    content = path.read_text(encoding="utf-8")
    reader = csv.DictReader(StringIO(content))

    rows = []
    for row in reader:
        # Convert each row to "key: value" format
        line = " | ".join(f"{k}: {v}" for k, v in row.items() if v)
        if line:
            rows.append(line)

    text = "\n".join(rows)

    return LoadedDocument(
        source=source,
        content=text,
        metadata={"format": "csv", "rows": len(rows)},
    )