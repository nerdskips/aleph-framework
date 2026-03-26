"""
Aleph Framework — Habits Store
=======================================
Saves operational habits after human escalation resolution.

Flow:
  1. LLM classifies: is this a unique case or a general pattern?
  2. If general: LLM generalizes the question (strips client-specific data)
  3. Dedup check: is there already a similar habit? (cosine similarity)
  4. Generate embedding for the generalized question
  5. Insert into Postgres (tsvector auto-populated by trigger)

Classification rules:
  - UNIQUE: specific to one client/order/incident (reclamação, pedido X, erro Y)
    → Saved with is_unique=true, NOT returned in searches
  - GENERAL: reusable pattern (horário, cardápio, prazo, política)
    → Saved with is_unique=false, returned in searches
"""

from __future__ import annotations

import json
import logging
from typing import Any

from core.habits.database import HabitsDatabase
from core.habits.embeddings import generate_embedding
from core.registry.schema import HabitsConfig

logger = logging.getLogger("aleph.habits")


# ---------------------------------------------------------------------------
# LLM Classification prompt
# ---------------------------------------------------------------------------

CLASSIFY_PROMPT = """[INSTRUÇÃO INTERNA — NÃO REPITA ESTE BLOCO]
Você é um classificador de hábitos operacionais.

Analise a pergunta do cliente e a resposta do atendente humano.
Classifique em GERAL ou UNICO:

GERAL — a resposta serve para qualquer cliente que pergunte algo parecido.
  Exemplos: horário de funcionamento, cardápio, prazo de entrega, formas de pagamento, política de troca.

UNICO — a resposta é específica para este cliente/pedido/caso.
  Exemplos: reclamação de pedido X, erro no cadastro do João, reembolso específico.

Se for GERAL, generalize a pergunta: remova dados específicos do cliente
(nome, telefone, número de pedido) e escreva uma pergunta genérica.

Responda SOMENTE em JSON, sem markdown, sem explicação:
{{"tipo": "GERAL" ou "UNICO", "pergunta_generalizada": "..." ou "", "resposta_generalizada": "..." ou ""}}

Pergunta do cliente: "{question}"
Instrução do atendente: "{instruction}"
"""


# ---------------------------------------------------------------------------
# Store habit
# ---------------------------------------------------------------------------

async def store_habit(
    db: HabitsDatabase,
    config: HabitsConfig,
    registry,
    client_id: str,
    original_question: str,
    human_instruction: str,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Classify, deduplicate, and store a new operational habit.

    Called after a human resolves an escalation.

    Args:
        db: HabitsDatabase instance
        config: HabitsConfig
        registry: AgentRegistry (for LLM calls)
        client_id: Agent client_id (for isolation)
        original_question: What the client asked
        human_instruction: What the human responded
        metadata: Optional metadata (context, tags, etc)

    Returns:
        Dict with stored habit data, or None if dedup/error
    """
    # --- Step 1: Classify via LLM ---
    classification = await _classify_habit(
        registry=registry,
        question=original_question,
        instruction=human_instruction,
    )

    if not classification:
        logger.warning("Habit classification failed, skipping store")
        return None

    is_unique = classification["tipo"] == "UNICO"

    if is_unique:
        # Unique cases: store as-is with flag
        question = original_question
        answer = human_instruction
        logger.info("Habit classified as UNIQUE: %s", question[:60])
    else:
        # General cases: use generalized versions
        question = classification.get("pergunta_generalizada") or original_question
        answer = classification.get("resposta_generalizada") or human_instruction
        logger.info("Habit classified as GENERAL: %s", question[:60])

    # --- Step 2: Generate embedding (on generalized question) ---
    try:
        embedding = await generate_embedding(question, config)
    except RuntimeError as e:
        logger.error("Embedding failed, skipping store: %s", e)
        return None

    # --- Step 3: Dedup check (only for general habits) ---
    if not is_unique:
        is_dup = await _check_dedup(
            db=db,
            client_id=client_id,
            embedding=embedding,
            threshold=config.dedup_threshold,
        )
        if is_dup:
            logger.info(
                "Habit dedup: similar habit already exists (threshold=%.3f), skipping",
                config.dedup_threshold,
            )
            return None

    # --- Step 4: Insert ---
    habit_data = await _insert_habit(
        db=db,
        client_id=client_id,
        question=question,
        answer=answer,
        human_instruction=human_instruction,
        is_unique=is_unique,
        embedding=embedding,
        metadata=metadata or {},
    )

    if habit_data:
        logger.info(
            "Habit stored: id=%s, type=%s, question=%s",
            habit_data["id"],
            "UNIQUE" if is_unique else "GENERAL",
            question[:60],
        )

    return habit_data


# ---------------------------------------------------------------------------
# Internal: LLM classification
# ---------------------------------------------------------------------------

async def _classify_habit(
    registry,
    question: str,
    instruction: str,
) -> dict | None:
    """Use LLM to classify habit as GERAL or UNICO."""
    from core.engine.runner import run_agent

    prompt = CLASSIFY_PROMPT.format(
        question=question,
        instruction=instruction,
    )

    try:
        result = await run_agent(registry, prompt, message_history=None)
        response_text = result.response.strip()

        # Strip markdown code fences if present
        if response_text.startswith("```"):
            response_text = response_text.split("\n", 1)[-1]
        if response_text.endswith("```"):
            response_text = response_text.rsplit("```", 1)[0]
        response_text = response_text.strip()

        data = json.loads(response_text)

        # Validate structure
        if "tipo" not in data or data["tipo"] not in ("GERAL", "UNICO"):
            logger.warning("Invalid classification response: %s", response_text[:200])
            return None

        return data

    except json.JSONDecodeError:
        logger.warning("Classification response not valid JSON: %s", result.response[:200])
        return None
    except Exception as e:
        logger.error("Classification LLM call failed: %s", str(e)[:200])
        return None


# ---------------------------------------------------------------------------
# Internal: Dedup check
# ---------------------------------------------------------------------------

async def _check_dedup(
    db: HabitsDatabase,
    client_id: str,
    embedding: list[float],
    threshold: float,
) -> bool:
    """Check if a similar habit already exists (cosine distance < threshold).

    Returns True if duplicate found.
    """
    # Cosine distance: 0 = identical, 2 = opposite
    # threshold 0.020 means very similar (production-validated)
    query = """
        SELECT id, (embedding <=> $1::vector) AS distance
        FROM operational_habits
        WHERE client_id = $2
          AND is_unique = false
          AND embedding IS NOT NULL
        ORDER BY embedding <=> $1::vector
        LIMIT 1;
    """

    try:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                str(embedding),
                client_id,
            )

        if row and row["distance"] < threshold:
            logger.debug(
                "Dedup hit: id=%s distance=%.4f (threshold=%.4f)",
                row["id"], row["distance"], threshold,
            )
            return True

        return False

    except Exception as e:
        logger.error("Dedup check failed: %s", str(e)[:200])
        return False  # Don't block store on dedup failure


# ---------------------------------------------------------------------------
# Internal: Insert
# ---------------------------------------------------------------------------

async def _insert_habit(
    db: HabitsDatabase,
    client_id: str,
    question: str,
    answer: str,
    human_instruction: str,
    is_unique: bool,
    embedding: list[float],
    metadata: dict,
) -> dict | None:
    """Insert a habit into Postgres.
    The tsvector (search_text) is auto-populated by the trigger."""
    query = """
        INSERT INTO operational_habits
            (client_id, question, answer, human_instruction, is_unique, embedding, metadata)
        VALUES
            ($1, $2, $3, $4, $5, $6::vector, $7::jsonb)
        RETURNING id, question, answer, is_unique, created_at;
    """

    try:
        async with db.pool.acquire() as conn:
            row = await conn.fetchrow(
                query,
                client_id,
                question,
                answer,
                human_instruction,
                is_unique,
                str(embedding),
                json.dumps(metadata, ensure_ascii=False),
            )

        if row:
            return {
                "id": row["id"],
                "question": row["question"],
                "answer": row["answer"],
                "is_unique": row["is_unique"],
                "created_at": str(row["created_at"]),
            }

        return None

    except Exception as e:
        logger.error("Habit insert failed: %s", str(e)[:200])
        return None