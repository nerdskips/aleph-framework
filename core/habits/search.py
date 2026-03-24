"""
Zuper Agent Framework — Habits Search
========================================
Hybrid search using Reciprocal Rank Fusion (RRF).

Combines two search strategies:
  - Semantic search: pgvector cosine similarity on embeddings
  - Full-text search: tsvector with Portuguese stemming + unaccent

RRF formula: score = 1/(k+rank_semantic) + 1/(k+rank_fulltext)
  - k=60 (default, production-validated)
  - Higher k = more weight to lower-ranked results (smoother)

Only returns GENERAL habits (is_unique=false).
The search is fast — no LLM involved, pure SQL.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from core.habits.database import HabitsDatabase
from core.habits.embeddings import generate_embedding
from core.registry.schema import HabitsConfig

logger = logging.getLogger("zuper.habits")


# ---------------------------------------------------------------------------
# Search result
# ---------------------------------------------------------------------------

@dataclass
class HabitMatch:
    """A single habit match from hybrid search."""
    id: int
    question: str
    answer: str
    human_instruction: str
    rrf_score: float
    semantic_rank: int | None
    fulltext_rank: int | None
    metadata: dict


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------

async def search_habits(
    db: HabitsDatabase,
    config: HabitsConfig,
    client_id: str,
    query: str,
) -> list[HabitMatch]:
    """Search for matching operational habits using hybrid RRF.

    This is the main entry point for the pipeline. Called when:
      - habits.search_before_escalate=true AND guardrail triggers ESCALATE
      - Or directly by the agent via a search tool (future)

    Args:
        db: HabitsDatabase instance
        config: HabitsConfig with search parameters
        client_id: Agent client_id for isolation
        query: User's question (raw text)

    Returns:
        List of HabitMatch sorted by RRF score (best first).
        Empty list if no matches found.
    """
    # Generate embedding for the query
    try:
        query_embedding = await generate_embedding(query, config)
    except RuntimeError as e:
        logger.error("Search embedding failed: %s", e)
        return []

    # Call the hybrid search function
    try:
        async with db.pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT * FROM buscar_habito_hibrido($1, $2, $3::vector, $4, $5)",
                client_id,
                query,
                str(query_embedding),
                config.match_count,
                config.rrf_k,
            )
    except Exception as e:
        logger.error("Hybrid search failed: %s", str(e)[:200])
        return []

    # Parse results
    matches = []
    for row in rows:
        match = HabitMatch(
            id=row["id"],
            question=row["question"],
            answer=row["answer"],
            human_instruction=row["human_instruction"] or "",
            rrf_score=row["rrf_score"],
            semantic_rank=row["semantic_rank"],
            fulltext_rank=row["fulltext_rank"],
            metadata=row["metadata"] or {},
        )
        matches.append(match)

    if matches:
        logger.info(
            "Habit search: query='%s' → %d match(es), best=%.4f",
            query[:60], len(matches), matches[0].rrf_score,
        )
    else:
        logger.debug("Habit search: query='%s' → no matches", query[:60])

    return matches


async def search_and_format(
    db: HabitsDatabase,
    config: HabitsConfig,
    client_id: str,
    query: str,
) -> str | None:
    """Search habits and format the best match as a context string.

    Returns a formatted string for injection into the LLM prompt,
    or None if no matches found.

    Used by the pipeline when search_before_escalate=true:
      - If match found → inject as context, skip escalation
      - If no match → proceed with escalation
    """
    matches = await search_habits(db, config, client_id, query)

    if not matches:
        return None

    best = matches[0]

    # Format for LLM injection
    result = (
        f"[HÁBITO OPERACIONAL ENCONTRADO]\n"
        f"Pergunta similar anterior: \"{best.question}\"\n"
        f"Resposta validada pela equipe: \"{best.answer}\"\n"
        f"\n"
        f"Use esta informação para responder ao cliente. "
        f"NÃO mencione que consultou um banco de dados ou hábito."
    )

    logger.info(
        "Habit match injected: id=%d, score=%.4f, question='%s'",
        best.id, best.rrf_score, best.question[:60],
    )

    return result