"""
Zuper Agent Framework — Habits Embeddings
============================================
Generates text embeddings via Bifrost LLM gateway.

Uses the embedding model configured in habits.embedding_model
(default: openai/text-embedding-3-small, 1536 dimensions).

Bifrost routes to the appropriate provider transparently.
"""

from __future__ import annotations

import logging
import os

import httpx

from core.registry.schema import HabitsConfig

logger = logging.getLogger("zuper.habits")


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
    gateway_url = os.environ.get("BIFROST_URL", "http://localhost:8080/v1")
    api_key = os.environ.get("BIFROST_API_KEY", "dummy")

    url = f"{gateway_url}/embeddings"
    payload = {
        "model": config.embedding_model,
        "input": text,
        "dimensions": config.embedding_dimensions,
    }
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(url, json=payload, headers=headers)
            response.raise_for_status()
            data = response.json()

        embedding = data["data"][0]["embedding"]

        logger.debug(
            "Embedding generated: %d dims, model=%s, text=%s",
            len(embedding), config.embedding_model, text[:60],
        )

        return embedding

    except httpx.HTTPError as e:
        error = f"Embedding generation failed: {str(e)[:200]}"
        logger.error(error)
        raise RuntimeError(error) from e
    except (KeyError, IndexError) as e:
        error = f"Unexpected embedding response format: {e}"
        logger.error(error)
        raise RuntimeError(error) from e