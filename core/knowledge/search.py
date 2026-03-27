"""
Aleph Framework — Knowledge Search
====================================
Hybrid search RRF for knowledge base.
Same strategy as habits, different table and fields.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.knowledge.database import KnowledgeDatabase
from core.knowledge.embeddings import generate_embedding
from core.registry.schema import KnowledgeConfig

logger = logging.getLogger("aleph.knowledge")


@dataclass
class KnowledgeMatch:
    """A single knowledge chunk match from hybrid search."""
    id: int
    content: str
    context: str
    source: str
    chunk_index: int
    rrf_score: float
    semantic_rank: int | None
    fulltext_rank: int | None
    metadata: dict


async def search_knowledge(
    db: KnowledgeDatabase,
    config: KnowledgeConfig,
    client_id: str,
    query: str,
) -> list[KnowledgeMatch]:
    """Search knowledge base using hybrid RRF.

    Args:
        db: KnowledgeDatabase instance
        config: KnowledgeConfig
        client_id: Agent client_id for isolation
        query: User's question

    Returns:
        List of KnowledgeMatch sorted by RRF score (best first).
    """
    try:
        query_embedding = await generate_embedding(query, config)
    except RuntimeError as e:
        logger.error("Knowledge embedding failed: %s", e)
        return []

    try:
        async with db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM buscar_conhecimento_hibrido($1, $2, $3::vector, $4, $5)",
                client_id,
                query,
                str(query_embedding),
                config.match_count,
                config.rrf_k,
            )
    except Exception as e:
        logger.error("Knowledge search failed: %s", str(e)[:200])
        return []

    matches = []
    for row in rows:
        matches.append(KnowledgeMatch(
            id=row["id"],
            content=row["content"],
            context=row["context"] or "",
            source=row["source"] or "",
            chunk_index=row["chunk_index"] or 0,
            rrf_score=row["rrf_score"],
            semantic_rank=row["semantic_rank"],
            fulltext_rank=row["fulltext_rank"],
            metadata=row["metadata"] or {},
        ))

    if matches:
        logger.info(
            "Knowledge search: query='%s' → %d match(es), best=%.4f",
            query[:60], len(matches), matches[0].rrf_score,
        )
    else:
        logger.debug("Knowledge search: query='%s' → no matches", query[:60])

    return matches


async def search_and_format(
    db: KnowledgeDatabase,
    config: KnowledgeConfig,
    client_id: str,
    query: str,
) -> str | None:
    """Search knowledge and format results for LLM injection.

    Returns formatted context string or None if no matches.
    """
    matches = await search_knowledge(db, config, client_id, query)

    if not matches:
        return None

    # Filter by similarity threshold
    # RRF scores are relative, so we use a simple cutoff
    # A single-source match has max score ~1/61 ≈ 0.016
    # A dual-source match has max score ~2/61 ≈ 0.033
    # We keep all results and let the LLM decide relevance

    chunks = []
    for i, match in enumerate(matches, 1):
        source_info = f"Fonte: {match.source}" if match.source else "Fonte: base de conhecimento"
        if match.context:
            source_info = f"{match.context} — {source_info}"

        # Sanitize content to avoid encoding issues
        clean_content = match.content.encode('utf-8', errors='replace').decode('utf-8')
        chunks.append(f"[{i}] ({source_info})\n{clean_content}")

    result = (
        "[BASE DE CONHECIMENTO]\n"
        "As informações abaixo foram encontradas na base de dados do negócio.\n"
        "Use-as para responder o cliente. Se a resposta necessária NÃO estiver "
        "abaixo, diga que vai verificar — NUNCA invente.\n\n"
        + "\n\n".join(chunks)
    )

    logger.info(
        "Knowledge context injected: %d chunks, %d chars",
        len(matches), len(result),
    )

    return result