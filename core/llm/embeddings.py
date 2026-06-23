"""
Aleph Framework — Unified Embedding Provider
=============================================
Provider-agnostic text embeddings via AsyncOpenAI.
Uses the same env-var detection as llm_router.py.

Supported providers (in detection order):
  1. LLM_PROVIDER env var override
  2. BIFROST_URL — Bifrost gateway
  3. OPENAI_API_KEY — OpenAI direct
  4. GEMINI_API_KEY — Gemini via OpenAI-compatible endpoint
  5. DEEPSEEK_API_KEY — DeepSeek
  6. OPENROUTER_API_KEY — OpenRouter
  7. LLM_BASE_URL + LLM_API_KEY — custom endpoint
"""

from __future__ import annotations

import logging
import os

from openai import AsyncOpenAI

logger = logging.getLogger("aleph.llm.embeddings")

_PROVIDER_MAP = {
    "bifrost": {
        "env_url": "BIFROST_URL",
        "env_key": "BIFROST_API_KEY",
        "default_url": "http://localhost:8080/v1",
        "default_key": "dummy",
    },
    "openai": {
        "env_url": None,
        "env_key": "OPENAI_API_KEY",
        "default_url": "https://api.openai.com/v1",
        "default_key": "",
    },
    "gemini": {
        "env_url": None,
        "env_key": "GEMINI_API_KEY",
        "default_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "default_key": "",
    },
    "deepseek": {
        "env_url": None,
        "env_key": "DEEPSEEK_API_KEY",
        "default_url": "https://api.deepseek.com/v1",
        "default_key": "",
    },
    "openrouter": {
        "env_url": None,
        "env_key": "OPENROUTER_API_KEY",
        "default_url": "https://openrouter.ai/api/v1",
        "default_key": "",
    },
}


def _resolve_embedding_credentials() -> tuple[str, str]:
    """Resolve (base_url, api_key) for embedding calls from environment.

    Returns:
        Tuple of (base_url, api_key) for AsyncOpenAI

    Raises:
        RuntimeError: If no provider credentials are found
    """
    # 1. Explicit provider override
    provider = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if provider and provider in _PROVIDER_MAP:
        p = _PROVIDER_MAP[provider]
        url = os.environ.get(p["env_url"], p["default_url"]) if p["env_url"] else p["default_url"]
        key = os.environ.get(p["env_key"], p["default_key"])
        return url, key

    # 2. Bifrost — explicit URL takes precedence
    bifrost_url = os.environ.get("BIFROST_URL", "")
    if bifrost_url:
        return bifrost_url, os.environ.get("BIFROST_API_KEY", "dummy")

    # 3. Direct API keys — checked in priority order
    for pname in ("openai", "gemini", "deepseek", "openrouter"):
        p = _PROVIDER_MAP[pname]
        key = os.environ.get(p["env_key"], "")
        if key:
            return p["default_url"], key

    # 4. Custom endpoint
    custom_url = os.environ.get("LLM_BASE_URL", "")
    if custom_url:
        return custom_url, os.environ.get("LLM_API_KEY", "")

    raise RuntimeError(
        "No LLM provider configured for embeddings. "
        "Set BIFROST_URL, OPENAI_API_KEY, or another provider key in .env"
    )


async def generate_embedding(
    text: str,
    model: str,
    dimensions: int,
) -> list[float]:
    """Generate a text embedding via the configured LLM provider.

    Args:
        text: Text to embed
        model: Embedding model name (e.g. "text-embedding-3-small")
        dimensions: Vector size (e.g. 1536)

    Returns:
        Embedding vector as list of floats

    Raises:
        RuntimeError: If provider is not configured or call fails
    """
    base_url, api_key = _resolve_embedding_credentials()

    client = AsyncOpenAI(base_url=base_url, api_key=api_key)

    try:
        response = await client.embeddings.create(
            model=model,
            input=text,
            dimensions=dimensions,
        )
        embedding = response.data[0].embedding
        logger.debug(
            "Embedding: %d dims, model=%s, text=%s",
            len(embedding), model, text[:60],
        )
        return embedding
    except Exception as e:
        error = f"Embedding generation failed: {str(e)[:200]}"
        logger.error(error)
        raise RuntimeError(error) from e
    finally:
        await client.close()
