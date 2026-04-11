"""
Aleph Framework — PDF Text Extraction
=======================================
Downloads a PDF and extracts text using pypdf (lazy import — optional dep).
Falls back gracefully if pypdf is not installed.
"""

from __future__ import annotations

import io
import logging

import httpx

from core.registry.schema import MediaConfig

logger = logging.getLogger("aleph.media.pdf")


async def extract_pdf_text(url: str, config: MediaConfig) -> str:
    """Download PDF and extract plain text up to pdf_max_pages.

    Args:
        url: Direct PDF URL from Z-API
        config: MediaConfig with pdf_max_pages and max_file_size_mb

    Returns:
        Extracted text string. Returns a Portuguese fallback string on failure.
    """
    try:
        import pypdf  # lazy: optional dep (pip install pypdf)
    except ImportError:
        logger.warning("pypdf not installed — PDF processing unavailable")
        return "[PDF recebido, mas não foi possível processar: instale pypdf]"

    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        pdf_bytes = resp.content

    size_mb = len(pdf_bytes) / (1024 * 1024)
    if size_mb > config.max_file_size_mb:
        logger.warning("PDF too large (%.1fMB > %dMB limit), skipping", size_mb, config.max_file_size_mb)
        return "[PDF muito grande para processar]"

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    total_pages = len(reader.pages)
    pages_to_read = reader.pages[: config.pdf_max_pages]

    extracted = []
    for i, page in enumerate(pages_to_read):
        page_text = page.extract_text() or ""
        if page_text.strip():
            extracted.append(page_text.strip())

    if not extracted:
        return "[PDF sem texto extraível]"

    text = "\n\n".join(extracted)
    if total_pages > config.pdf_max_pages:
        text += f"\n\n[... {total_pages - config.pdf_max_pages} página(s) omitida(s) — limite: {config.pdf_max_pages}]"

    logger.info("PDF extracted: %d pages, %d chars", min(total_pages, config.pdf_max_pages), len(text))
    return text
