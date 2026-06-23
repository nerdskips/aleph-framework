"""
Aleph Framework — Habits Embeddings
============================================
Delegates to core.llm.embeddings for provider-agnostic embedding generation.
"""

from __future__ import annotations

import logging

from core.llm.embeddings import generate_embedding as _shared_generate
from core.registry.schema import HabitsConfig

logger = logging.getLogger("aleph.habits")


async def generate_embedding(
    text: str,
    config: HabitsConfig,
) -> list[float]:
    """Generate an embedding vector for a text string.

    Args:
        text: Text to embed (typically the generalized question)
        config: HabitsConfig with model and dimensions

    Returns:
        List of floats (embedding vector)

    Raises:
        RuntimeError: If embedding generation fails
    """
    return await _shared_generate(text, config.embedding_model, config.embedding_dimensions)
