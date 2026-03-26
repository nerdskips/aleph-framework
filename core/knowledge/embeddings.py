"""
Aleph Framework — Knowledge Embeddings
========================================
Generates text embeddings for knowledge base chunks.
Independent copy from habits — same interface, separate module.
"""

from __future__ import annotations

import logging
import os

import httpx

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
    # Detect provider: Bifrost or direct API key
    gateway_url = os.environ.get("BIFROST_URL", "")
    api_key = os.environ.get("BIFROST_API_KEY", "")

    if not gateway_url:
        # Try direct providers
        if os.environ.get("OPENAI_API_KEY"):
            gateway_url = "https://api.openai.com/v1"
            api_key = os.environ["OPENAI_API_KEY"]
        elif os.environ.get("GEMINI_API_KEY"):
            gateway_url = "https://generativelanguage.googleapis.com/v1beta/openai"
            api_key = os.environ["GEMINI_API_KEY"]
        else:
            raise RuntimeError(
                "No embedding provider found. Set BIFROST_URL or OPENAI_API_KEY."
            )

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
        error = f"Knowledge embedding failed: {str(e)[:200]}"
        logger.error(error)
        raise RuntimeError(error) from e
    except (KeyError, IndexError) as e:
        error = f"Unexpected embedding response format: {e}"
        logger.error(error)
        raise RuntimeError(error) from e