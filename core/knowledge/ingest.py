"""
Aleph Framework — Knowledge Ingestion
========================================
Parses files, chunks text, generates contextual enrichment,
and stores chunks with embeddings in Postgres.

Chunking strategy: recursive splitting with overlap.
Contextual enrichment: prepend document context to each chunk
before generating embeddings (Anthropic contextual retrieval pattern).
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass

from core.knowledge.database import KnowledgeDatabase
from core.knowledge.embeddings import generate_embedding
from core.knowledge.loader import LoadedDocument
from core.registry.schema import KnowledgeConfig

logger = logging.getLogger("aleph.knowledge")


# ---------------------------------------------------------------------------
# Chunk dataclass
# ---------------------------------------------------------------------------

@dataclass
class Chunk:
    """A text chunk ready for embedding and storage."""
    content: str
    context: str        # contextual prefix (section title, doc summary)
    source: str
    chunk_index: int
    metadata: dict


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def chunk_document(
    doc: LoadedDocument,
    chunk_size: int = 500,
    chunk_overlap: int = 75,
) -> list[Chunk]:
    """Split a document into chunks using recursive splitting.

    Strategy:
      1. Split by double newline (paragraphs)
      2. If a paragraph exceeds chunk_size, split by single newline
      3. If still too long, split by sentence ('. ')
      4. Merge small consecutive chunks up to chunk_size

    Args:
        doc: LoadedDocument with content
        chunk_size: Target chunk size in characters (not tokens, simpler)
        chunk_overlap: Overlap in characters between chunks

    Returns:
        List of Chunk objects
    """
    text = doc.content.strip()
    if not text:
        return []

    # Step 1: split into segments by natural boundaries
    segments = _recursive_split(text, chunk_size)

    # Step 2: merge small segments and apply overlap
    chunks = _merge_with_overlap(segments, chunk_size, chunk_overlap)

    # Step 3: detect context for each chunk (section headers)
    result = []
    current_context = doc.source  # default context is filename

    for i, chunk_text in enumerate(chunks):
        # Try to detect section header from the chunk
        detected = _detect_section_header(chunk_text)
        if detected:
            current_context = f"{doc.source} — {detected}"

        result.append(Chunk(
            content=chunk_text.strip(),
            context=current_context,
            source=doc.source,
            chunk_index=i,
            metadata=doc.metadata.copy(),
        ))

    logger.info(
        "Chunked '%s': %d chars → %d chunks (size=%d, overlap=%d)",
        doc.source, len(text), len(result), chunk_size, chunk_overlap,
    )

    return result


def _recursive_split(text: str, max_size: int) -> list[str]:
    """Recursively split text by natural boundaries."""
    # If text fits, return as-is
    if len(text) <= max_size:
        return [text]

    # Try splitting by double newline (paragraphs)
    parts = text.split("\n\n")
    if len(parts) > 1:
        result = []
        for part in parts:
            if len(part) <= max_size:
                result.append(part)
            else:
                result.extend(_recursive_split(part, max_size))
        return result

    # Try splitting by single newline
    parts = text.split("\n")
    if len(parts) > 1:
        result = []
        for part in parts:
            if len(part) <= max_size:
                result.append(part)
            else:
                result.extend(_recursive_split(part, max_size))
        return result

    # Last resort: split by sentence
    sentences = re.split(r'(?<=[.!?])\s+', text)
    if len(sentences) > 1:
        return sentences

    # Absolute last resort: hard split
    result = []
    for i in range(0, len(text), max_size):
        result.append(text[i:i + max_size])
    return result


def _merge_with_overlap(
    segments: list[str],
    max_size: int,
    overlap: int,
) -> list[str]:
    """Merge small segments and apply overlap between chunks."""
    if not segments:
        return []

    chunks = []
    current = segments[0]

    for segment in segments[1:]:
        # If adding this segment fits, merge
        if len(current) + len(segment) + 1 <= max_size:
            current = current + "\n" + segment
        else:
            # Emit current chunk
            chunks.append(current)
            # Start new chunk with overlap from end of previous
            if overlap > 0 and len(current) > overlap:
                overlap_text = current[-overlap:]
                # Find a clean break point in the overlap
                last_space = overlap_text.rfind(" ")
                if last_space > 0:
                    overlap_text = overlap_text[last_space + 1:]
                current = overlap_text + "\n" + segment
            else:
                current = segment

    # Don't forget the last chunk
    if current.strip():
        chunks.append(current)

    return chunks


def _detect_section_header(text: str) -> str | None:
    """Try to detect a section header (markdown # or ALL CAPS line)."""
    first_line = text.split("\n")[0].strip()

    # Markdown headers
    if first_line.startswith("#"):
        return first_line.lstrip("#").strip()

    # ALL CAPS short line (likely a section title)
    if first_line.isupper() and len(first_line) < 80:
        return first_line.title()

    return None


# ---------------------------------------------------------------------------
# Ingestion pipeline
# ---------------------------------------------------------------------------

async def ingest_document(
    db: KnowledgeDatabase,
    config: KnowledgeConfig,
    client_id: str,
    doc: LoadedDocument,
    clear_existing: bool = True,
) -> int:
    """Ingest a single document: chunk, embed, store.

    Args:
        db: KnowledgeDatabase instance
        config: KnowledgeConfig
        client_id: Agent client_id
        doc: LoadedDocument to ingest
        clear_existing: If True, removes existing chunks from this source first

    Returns:
        Number of chunks stored
    """
    schema = config.schema
    table = config.table_name
    full_table = f"{schema}.{table}" if schema != "public" else table

    # Clear existing chunks from this source
    if clear_existing:
        async with db.pool.acquire() as conn:
            deleted = await conn.execute(
                f"DELETE FROM {full_table} WHERE client_id = $1 AND source = $2",
                client_id, doc.source,
            )
            logger.debug("Cleared existing chunks for %s: %s", doc.source, deleted)

    # Chunk the document
    chunks = chunk_document(
        doc,
        chunk_size=config.chunk_size,
        chunk_overlap=config.chunk_overlap,
    )

    if not chunks:
        logger.warning("No chunks produced from %s", doc.source)
        return 0

    # Generate embeddings and store
    stored = 0
    for chunk in chunks:
        try:
            # Contextual embedding: embed context + content together
            embed_text = f"{chunk.context}\n{chunk.content}" if chunk.context else chunk.content
            embedding = await generate_embedding(embed_text, config)

            async with db.pool.acquire() as conn:
                await conn.execute(
                    f"""
                    INSERT INTO {full_table}
                        (client_id, content, context, source, chunk_index, embedding, metadata)
                    VALUES ($1, $2, $3, $4, $5, $6::vector, $7)
                    """,
                    client_id,
                    chunk.content,
                    chunk.context,
                    chunk.source,
                    chunk.chunk_index,
                    str(embedding),
                    chunk.metadata,
                )

            stored += 1
            logger.debug(
                "Stored chunk %d/%d from %s (%d chars)",
                stored, len(chunks), doc.source, len(chunk.content),
            )

        except Exception as e:
            logger.error(
                "Failed to store chunk %d from %s: %s",
                chunk.chunk_index, doc.source, str(e)[:200],
            )

    logger.info(
        "Ingested '%s': %d/%d chunks stored",
        doc.source, stored, len(chunks),
    )

    return stored


async def ingest_documents(
    db: KnowledgeDatabase,
    config: KnowledgeConfig,
    client_id: str,
    docs: list[LoadedDocument],
    clear_existing: bool = True,
) -> int:
    """Ingest multiple documents.

    Returns:
        Total number of chunks stored
    """
    total = 0
    for doc in docs:
        count = await ingest_document(db, config, client_id, doc, clear_existing)
        total += count

    logger.info("Ingestion complete: %d documents, %d total chunks", len(docs), total)
    return total