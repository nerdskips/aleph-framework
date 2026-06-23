"""Tests: Phase 1 — Unified embedding provider (LLM agnostic)."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.llm.embeddings import generate_embedding, _resolve_embedding_credentials


# ---------------------------------------------------------------------------
# _resolve_embedding_credentials
# ---------------------------------------------------------------------------

def test_resolves_openai_from_api_key(monkeypatch):
    monkeypatch.delenv("BIFROST_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    url, key = _resolve_embedding_credentials()
    assert url == "https://api.openai.com/v1"
    assert key == "sk-test"


def test_resolves_bifrost_from_url(monkeypatch):
    monkeypatch.setenv("BIFROST_URL", "http://bifrost:8080/v1")
    monkeypatch.setenv("BIFROST_API_KEY", "dummy")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    url, key = _resolve_embedding_credentials()
    assert url == "http://bifrost:8080/v1"
    assert key == "dummy"


def test_resolves_provider_env_override(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "sk-env")
    url, key = _resolve_embedding_credentials()
    assert url == "https://api.openai.com/v1"
    assert key == "sk-env"


def test_raises_when_no_provider(monkeypatch):
    for var in ["BIFROST_URL", "OPENAI_API_KEY", "GEMINI_API_KEY",
                "DEEPSEEK_API_KEY", "OPENROUTER_API_KEY", "LLM_BASE_URL", "LLM_PROVIDER"]:
        monkeypatch.delenv(var, raising=False)
    with pytest.raises(RuntimeError, match="No LLM provider"):
        _resolve_embedding_credentials()


# ---------------------------------------------------------------------------
# generate_embedding
# ---------------------------------------------------------------------------

async def test_generate_embedding_calls_openai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("BIFROST_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    mock_response = MagicMock()
    mock_response.data = [MagicMock(embedding=[0.1, 0.2, 0.3])]

    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(return_value=mock_response)

    with patch("core.llm.embeddings.AsyncOpenAI", return_value=mock_client):
        result = await generate_embedding("hello world", "text-embedding-3-small", 1536)

    assert result == [0.1, 0.2, 0.3]
    mock_client.embeddings.create.assert_called_once_with(
        model="text-embedding-3-small",
        input="hello world",
        dimensions=1536,
    )


async def test_generate_embedding_raises_on_http_error(monkeypatch):
    import httpx
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("BIFROST_URL", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)

    mock_client = AsyncMock()
    mock_client.embeddings.create = AsyncMock(
        side_effect=Exception("connection refused")
    )

    with patch("core.llm.embeddings.AsyncOpenAI", return_value=mock_client):
        with pytest.raises(RuntimeError, match="Embedding generation failed"):
            await generate_embedding("hello", "text-embedding-3-small", 1536)


# ---------------------------------------------------------------------------
# habits/embeddings delegation
# ---------------------------------------------------------------------------

async def test_habits_embedding_delegates_to_shared(monkeypatch):
    from core.registry.schema import HabitsConfig
    from core.habits.embeddings import generate_embedding as habits_embed

    config = HabitsConfig(
        enabled=True,
        embedding_model="text-embedding-3-small",
        embedding_dimensions=1536,
    )

    with patch("core.habits.embeddings._shared_generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = [0.1, 0.2]
        result = await habits_embed("test text", config)

    mock_gen.assert_called_once_with("test text", "text-embedding-3-small", 1536)
    assert result == [0.1, 0.2]


# ---------------------------------------------------------------------------
# knowledge/embeddings delegation
# ---------------------------------------------------------------------------

async def test_knowledge_embedding_delegates_to_shared(monkeypatch):
    from core.registry.schema import KnowledgeConfig
    from core.knowledge.embeddings import generate_embedding as knowledge_embed

    config = KnowledgeConfig(enabled=True)

    with patch("core.knowledge.embeddings._shared_generate", new_callable=AsyncMock) as mock_gen:
        mock_gen.return_value = [0.5, 0.6]
        result = await knowledge_embed("test chunk", config)

    mock_gen.assert_called_once_with(
        "test chunk",
        config.embedding_model,
        config.embedding_dimensions,
    )
    assert result == [0.5, 0.6]
