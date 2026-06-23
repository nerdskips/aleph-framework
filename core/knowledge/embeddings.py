"""
Aleph Framework — Knowledge Embeddings
========================================
Delegates to core.llm.embeddings for provider-agnostic embedding generation.
"""

from __future__ import annotations

import logging

from core.llm.embeddings import generate_embedding as _shared_generate
from core.registry.schema import KnowledgeConfig

logger = logging.getLogger("aleph.knowledge")


async def generate_embedding(
    text: str,
    config: KnowledgeConfig,
) -> list[float]:
    """Generate an embedding vector for a text string.

    Args:
        text: Text to embed
        config: KnowledgeConfig with model and dimensions

    Returns:
        List of floats (embedding vector)

    Raises:
        RuntimeError: If embedding generation fails
    """
    return await _shared_generate(text, config.embedding_model, config.embedding_dimensions)
