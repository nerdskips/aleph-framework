# Phase 1 — LLM Agnosticism & Channel Abstraction

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Remove Bifrost as a hard dependency and make the messaging layer channel-agnostic, so the framework runs with any OpenAI-compatible provider and can accept messages from any channel (WhatsApp, Telegram, etc.), not just Z-API.

**Architecture:** Task 1 consolidates all embedding calls into a single provider-aware function in `core/llm/embeddings.py` that uses the same env-var detection logic as the existing LLM router. Task 2 introduces `IncomingMessage` + `ChannelSender` interfaces in `core/channels/`, moves Z-API logic there as one concrete adapter, and removes `core/messaging/` entirely.

**Tech Stack:** Python 3.12, `openai` SDK (`AsyncOpenAI`), `pydantic`, `pytest-asyncio`, `httpx`

## Global Constraints

- `from __future__ import annotations` must be the first line of every Python file
- All I/O is async
- No `print()` — use `logger`
- Redis keys keep prefix `aleph:{client_id}:`
- New features default OFF — this plan only moves existing code, no new YAML fields needed
- Loggers follow `logging.getLogger("aleph.modulename")`
- `asyncio_mode = "auto"` in pyproject.toml — all `async def` test functions run natively
- Run tests from repo root: `python -m pytest tests/ -v`
- Branch: `feature/phase-1-llm-agnostic-channel-abstraction`

---

## File Map

### Task 1 — Unified Embedding Provider
| Action | Path |
|--------|------|
| Create | `core/llm/embeddings.py` |
| Modify | `core/habits/embeddings.py` |
| Modify | `core/knowledge/embeddings.py` |
| Create | `tests/framework/test_embeddings.py` |

### Task 2 — Channel Abstraction
| Action | Path |
|--------|------|
| Create | `core/channels/__init__.py` |
| Create | `core/channels/base.py` |
| Create | `core/channels/zapi/__init__.py` |
| Create | `core/channels/zapi/adapter.py` |
| Create | `core/channels/zapi/sender.py` |
| Modify | `core/api/webhooks.py` |
| Modify | `core/engine/pipeline.py` (type hint only) |
| Modify | `core/human/escalation.py` (type hint only) |
| Delete | `core/messaging/zapi_filter.py` |
| Delete | `core/messaging/zapi_send.py` |
| Delete | `core/messaging/__init__.py` |
| Create | `tests/framework/test_channels.py` |

---

## Task 1: Unified Embedding Provider

**Files:**
- Create: `core/llm/embeddings.py`
- Modify: `core/habits/embeddings.py`
- Modify: `core/knowledge/embeddings.py`
- Test: `tests/framework/test_embeddings.py`

**Interfaces:**
- Produces: `core.llm.embeddings.generate_embedding(text: str, model: str, dimensions: int) -> list[float]`
- Consumes: `openai.AsyncOpenAI`, `os.environ` provider detection
- The habits and knowledge modules keep their existing external signatures: `generate_embedding(text: str, config: HabitsConfig | KnowledgeConfig) -> list[float]`

---

- [ ] **Step 1.1: Create the branch**

```bash
git checkout main
git pull
git checkout -b feature/phase-1-llm-agnostic-channel-abstraction
```

---

- [ ] **Step 1.2: Write the failing tests for the unified embedding function**

Create `tests/framework/test_embeddings.py`:

```python
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
```

---

- [ ] **Step 1.3: Run the tests to confirm they fail**

```bash
python -m pytest tests/framework/test_embeddings.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.llm.embeddings'`

---

- [ ] **Step 1.4: Create `core/llm/embeddings.py`**

```python
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
```

---

- [ ] **Step 1.5: Run embedding tests — core module only**

```bash
python -m pytest tests/framework/test_embeddings.py::test_resolves_openai_from_api_key tests/framework/test_embeddings.py::test_resolves_bifrost_from_url tests/framework/test_embeddings.py::test_raises_when_no_provider tests/framework/test_embeddings.py::test_generate_embedding_calls_openai -v
```

Expected: 4 PASSED

---

- [ ] **Step 1.6: Update `core/habits/embeddings.py` to delegate**

Replace the entire file content:

```python
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
```

---

- [ ] **Step 1.7: Update `core/knowledge/embeddings.py` to delegate**

Replace the entire file content:

```python
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
```

---

- [ ] **Step 1.8: Run all embedding tests**

```bash
python -m pytest tests/framework/test_embeddings.py -v
```

Expected: All 8 tests PASSED

---

- [ ] **Step 1.9: Run the full test suite to check for regressions**

```bash
python -m pytest tests/ -v
```

Expected: All existing tests pass (zero new failures)

---

- [ ] **Step 1.10: Commit Task 1**

```bash
git add core/llm/embeddings.py core/habits/embeddings.py core/knowledge/embeddings.py tests/framework/test_embeddings.py
git commit -m "feat(llm): unified provider-agnostic embedding function

Replaces hardcoded Bifrost calls in habits/embeddings.py and
knowledge/embeddings.py with a shared core/llm/embeddings.py
that uses the same env-var provider detection as llm_router.py.

Both domain modules now delegate to core.llm.embeddings and keep
their existing external signatures unchanged."
```

---

## Task 2: Channel Abstraction Layer

**Files:**
- Create: `core/channels/__init__.py`
- Create: `core/channels/base.py`
- Create: `core/channels/zapi/__init__.py`
- Create: `core/channels/zapi/adapter.py`
- Create: `core/channels/zapi/sender.py`
- Modify: `core/api/webhooks.py`
- Modify: `core/engine/pipeline.py` (type hint only)
- Modify: `core/human/escalation.py` (type hint only)
- Delete: `core/messaging/zapi_filter.py`, `core/messaging/zapi_send.py`, `core/messaging/__init__.py`
- Test: `tests/framework/test_channels.py`

**Interfaces:**
- Produces: `IncomingMessage` dataclass (channel-agnostic message), `ChannelSender` ABC
- Produces: `ZAPIAdapter` class with `extract(payload) -> IncomingMessage | None`, `should_filter(msg, config) -> str | None`, `is_human_reply(msg, phones) -> bool`, `is_human_takeover(msg) -> bool`
- Produces: `ZAPISender(ChannelSender)` — same public API as before
- `webhooks.py` now imports from `core.channels.zapi` instead of `core.messaging`
- `pipeline.py` and `escalation.py` type-annotate `sender` as `ChannelSender`

---

- [ ] **Step 2.1: Write the failing channel tests**

Create `tests/framework/test_channels.py`:

```python
"""Tests: Phase 1 — Channel abstraction (IncomingMessage, ZAPIAdapter, ZAPISender)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.channels.base import ChannelSender, IncomingMessage
from core.channels.zapi.adapter import ZAPIAdapter
from core.channels.zapi.sender import ZAPISender
from core.registry.schema import FrameworkConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config() -> FrameworkConfig:
    return FrameworkConfig(
        client_id="test",
        agent={"name": "Test"},
        human={"enabled": False},
    )


# ---------------------------------------------------------------------------
# IncomingMessage
# ---------------------------------------------------------------------------

def test_incoming_message_required_fields():
    msg = IncomingMessage(
        sender_id="5511999999999",
        text="hello",
        message_id="msg-001",
        channel="whatsapp",
    )
    assert msg.sender_id == "5511999999999"
    assert msg.text == "hello"
    assert msg.message_id == "msg-001"
    assert msg.channel == "whatsapp"
    assert msg.is_from_agent is False
    assert msg.is_from_api is False
    assert msg.reference_message_id == ""
    assert msg.media_type is None
    assert msg.metadata == {}
    assert msg.raw == {}


def test_incoming_message_optional_fields():
    msg = IncomingMessage(
        sender_id="123",
        text="hi",
        message_id="m1",
        channel="telegram",
        is_from_agent=True,
        reference_message_id="ref-99",
        media_type="image",
        media_url="https://example.com/img.jpg",
        metadata={"chat_id": 42},
    )
    assert msg.is_from_agent is True
    assert msg.reference_message_id == "ref-99"
    assert msg.media_type == "image"
    assert msg.metadata["chat_id"] == 42


# ---------------------------------------------------------------------------
# ZAPIAdapter.extract
# ---------------------------------------------------------------------------

def test_extract_text_message():
    payload = {
        "phone": "5511999999999",
        "messageId": "zapi-001",
        "fromMe": False,
        "fromApi": False,
        "isGroup": False,
        "isNewsletter": False,
        "broadcast": False,
        "type": "ReceivedCallback",
        "text": {"message": "Hello world"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert msg is not None
    assert msg.sender_id == "5511999999999"
    assert msg.text == "Hello world"
    assert msg.message_id == "zapi-001"
    assert msg.channel == "whatsapp"
    assert msg.is_from_agent is False
    assert msg.is_from_api is False


def test_extract_returns_none_for_non_dict():
    assert ZAPIAdapter.extract("not a dict") is None  # type: ignore
    assert ZAPIAdapter.extract(None) is None  # type: ignore


def test_extract_audio_message():
    payload = {
        "phone": "5511999999999",
        "messageId": "audio-001",
        "fromMe": False,
        "fromApi": False,
        "isGroup": False,
        "isNewsletter": False,
        "broadcast": False,
        "type": "ReceivedCallback",
        "audio": {"audioUrl": "https://example.com/audio.ogg", "mimeType": "audio/ogg"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert msg is not None
    assert msg.text == "[audio]"
    assert msg.media_type == "audio"
    assert msg.media_url == "https://example.com/audio.ogg"


def test_extract_from_api_message():
    payload = {
        "phone": "5511999999999",
        "messageId": "api-001",
        "fromMe": True,
        "fromApi": True,
        "isGroup": False,
        "isNewsletter": False,
        "broadcast": False,
        "type": "ReceivedCallback",
        "text": {"message": "sent by bot"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert msg is not None
    assert msg.is_from_agent is True
    assert msg.is_from_api is True


# ---------------------------------------------------------------------------
# ZAPIAdapter.should_filter
# ---------------------------------------------------------------------------

def test_should_filter_group(monkeypatch):
    config = _minimal_config()
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": True, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "hi"},
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason == "filtered:group"


def test_should_filter_from_api():
    config = _minimal_config()
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": True, "fromApi": True,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "bot response"},
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason == "filtered:from_api"


def test_should_filter_no_text():
    config = _minimal_config()
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback",
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason == "filtered:no_text"


def test_should_not_filter_valid_message():
    config = _minimal_config()
    payload = {
        "phone": "5511999999999", "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "hello"},
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason is None


# ---------------------------------------------------------------------------
# ZAPIAdapter.is_human_takeover / is_human_reply
# ---------------------------------------------------------------------------

def test_is_human_takeover_from_me_not_api():
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": True, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "typing"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert ZAPIAdapter.is_human_takeover(msg) is True


def test_is_not_human_takeover_from_api():
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": True, "fromApi": True,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "bot"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert ZAPIAdapter.is_human_takeover(msg) is False


def test_is_human_reply_with_reference():
    payload = {
        "phone": "5534999999999",
        "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "resolved"},
        "referenceMessageId": "notif-001",
    }
    msg = ZAPIAdapter.extract(payload)
    responsible = ["5534999999999"]
    assert ZAPIAdapter.is_human_reply(msg, responsible) is True


def test_is_not_human_reply_wrong_phone():
    payload = {
        "phone": "5511000000000",
        "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "hi"},
        "referenceMessageId": "notif-001",
    }
    msg = ZAPIAdapter.extract(payload)
    responsible = ["5534999999999"]
    assert ZAPIAdapter.is_human_reply(msg, responsible) is False


# ---------------------------------------------------------------------------
# ZAPISender
# ---------------------------------------------------------------------------

def test_zapi_sender_is_channel_sender():
    config = _minimal_config()
    sender = ZAPISender(config)
    assert isinstance(sender, ChannelSender)


async def test_zapi_sender_dry_run_does_not_call_http():
    from core.registry.schema import DebugConfig
    config = _minimal_config()
    config.debug.dry_run = True
    sender = ZAPISender(config)

    with patch.object(sender, "_send_text", new_callable=AsyncMock) as mock_send:
        await sender.send_response("5511999999999", "hello")
        mock_send.assert_not_called()


async def test_zapi_sender_send_notification_returns_message_id():
    config = _minimal_config()
    config.debug.dry_run = False
    sender = ZAPISender(config)

    mock_response = {"zaapId": "abc", "messageId": "msg-returned-id"}
    with patch.object(sender, "_send_text", new_callable=AsyncMock, return_value=mock_response):
        msg_id = await sender.send_notification("5511999999999", "alert!")
        assert msg_id == "msg-returned-id"
```

---

- [ ] **Step 2.2: Run the tests to confirm they fail**

```bash
python -m pytest tests/framework/test_channels.py -v
```

Expected: `ModuleNotFoundError: No module named 'core.channels'`

---

- [ ] **Step 2.3: Create `core/channels/base.py`**

```python
"""
Aleph Framework — Channel Abstraction Base
==========================================
Channel-agnostic types for message input and output.

IncomingMessage — a normalized message from any channel.
ChannelSender   — abstract base for all channel-specific senders.

Adding a new channel:
  1. Create core/channels/<name>/adapter.py  — parse incoming payload → IncomingMessage
  2. Create core/channels/<name>/sender.py   — subclass ChannelSender
  3. Mount the channel's webhook route in core/api/webhooks.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    """Channel-agnostic representation of an incoming message.

    Attributes:
        sender_id: Who sent the message (phone number, Telegram user_id, etc.)
        text: Processed text content (may be empty for media-only messages)
        message_id: Unique ID used for anti-spam deduplication
        channel: Source channel identifier ("whatsapp", "telegram", etc.)
        is_from_agent: True if the message was sent by the agent itself
        is_from_api: True if sent via API/automation (not a real user)
        reference_message_id: ID of the quoted/replied-to message (HITL detection)
        media_type: "audio", "image", "pdf", or None
        media_url: URL to download media from
        media_mimetype: MIME type of the media
        metadata: Channel-specific extra fields (e.g. is_group, is_newsletter)
        raw: Full original payload from the channel (for channel-specific logic)
    """

    # Required
    sender_id: str
    text: str
    message_id: str
    channel: str

    # Optional with defaults
    is_from_agent: bool = False
    is_from_api: bool = False
    reference_message_id: str = ""
    media_type: str | None = None
    media_url: str | None = None
    media_mimetype: str | None = None
    metadata: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class ChannelSender(ABC):
    """Abstract base class for all channel-specific message senders.

    Subclass this for each channel (Z-API/WhatsApp, Telegram, SMS, etc.)
    and implement the three abstract methods.
    """

    @abstractmethod
    async def send_response(self, recipient_id: str, text: str) -> None:
        """Send a response message to a recipient.

        Args:
            recipient_id: Destination address (phone, Telegram user_id, etc.)
            text: Message text to send
        """
        ...

    @abstractmethod
    async def send_notification(self, recipient_id: str, text: str) -> str | None:
        """Send a notification message and return its ID.

        Used for HITL escalation notifications where we need to track
        the message ID to match a quoted reply later.

        Args:
            recipient_id: Destination address
            text: Notification text

        Returns:
            Message ID string if available, None otherwise
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close any open HTTP clients or connections."""
        ...
```

---

- [ ] **Step 2.4: Create `core/channels/__init__.py`**

```python
"""Aleph Framework — Channel abstraction layer."""

from __future__ import annotations

from core.channels.base import ChannelSender, IncomingMessage

__all__ = ["IncomingMessage", "ChannelSender"]
```

---

- [ ] **Step 2.5: Create `core/channels/zapi/adapter.py`**

This is a rewrite of `core/messaging/zapi_filter.py` using `IncomingMessage`.

```python
"""
Aleph Framework — Z-API Channel Adapter
========================================
Parses Z-API webhook payloads into IncomingMessage objects
and implements Z-API-specific filtering and detection logic.
"""

from __future__ import annotations

import logging

from core.channels.base import IncomingMessage
from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.channels.zapi")

CHANNEL = "whatsapp"


class ZAPIAdapter:
    """Stateless adapter for Z-API webhook payloads.

    All methods are class methods — no state, safe to call without instantiation.
    """

    @classmethod
    def extract(cls, payload: dict) -> IncomingMessage | None:
        """Parse a Z-API webhook payload into an IncomingMessage.

        Returns None if the payload is not a valid dict.
        """
        if not isinstance(payload, dict):
            return None

        media_type, media_url, media_mimetype = cls._extract_media_meta(payload)

        return IncomingMessage(
            sender_id=payload.get("phone", ""),
            text=cls._extract_text(payload),
            message_id=payload.get("messageId", payload.get("id", {}).get("id", "")),
            channel=CHANNEL,
            is_from_agent=payload.get("fromMe", False),
            is_from_api=payload.get("fromApi", False),
            reference_message_id=payload.get("referenceMessageId", ""),
            media_type=media_type,
            media_url=media_url,
            media_mimetype=media_mimetype,
            metadata={
                "type": payload.get("type", ""),
                "is_group": payload.get("isGroup", False),
                "is_newsletter": payload.get("isNewsletter", False),
                "is_broadcast": payload.get("broadcast", False),
            },
            raw=payload,
        )

    @classmethod
    def should_filter(cls, message: IncomingMessage, config: FrameworkConfig) -> str | None:
        """Check if a message should be filtered out.

        Returns None if message should be processed.
        Returns a reason string if message should be discarded.
        """
        messaging = config.messaging
        msg_type = message.metadata.get("type", "")

        # Filter by Z-API event type
        if msg_type in messaging.filter_types:
            return f"filtered_type:{msg_type}"

        always_filter = {
            "DeliveryCallback", "ReadCallback", "PresenceCallback",
            "StatusCallback", "ConnStatusCallback",
        }
        if msg_type in always_filter:
            return f"filtered_type:{msg_type}"

        # Filter groups / newsletters / broadcasts
        if messaging.filter_groups and message.metadata.get("is_group"):
            return "filtered:group"
        if messaging.filter_newsletters and message.metadata.get("is_newsletter"):
            return "filtered:newsletter"
        if messaging.filter_broadcasts and message.metadata.get("is_broadcast"):
            return "filtered:broadcast"

        # Filter Z-API-specific event types from raw payload
        raw = message.raw
        if messaging.filter_reactions and "reaction" in raw:
            return "filtered:reaction"
        if messaging.filter_edits and raw.get("isEdit"):
            return "filtered:edit"
        if raw.get("isStatusReply"):
            return "filtered:status_reply"
        if raw.get("waitingMessage"):
            return "filtered:waiting_message"
        if raw.get("pinEvent"):
            return "filtered:pin"
        if raw.get("eventMessage"):
            return "filtered:event"
        if raw.get("paymentInfo"):
            return "filtered:payment"
        if raw.get("notification"):
            return "filtered:notification"

        # Filter bot's own API-sent messages
        if message.is_from_agent and message.is_from_api:
            return "filtered:from_api"

        # Filter empty text — allow media through when media processing is enabled
        if not message.text:
            if config.media.enabled and message.media_type in [t.value for t in config.media.supported_types]:
                pass  # media will be processed pre-buffer
            else:
                return "filtered:no_text"

        if not message.sender_id:
            return "filtered:no_phone"

        return None

    @classmethod
    def is_human_takeover(cls, message: IncomingMessage) -> bool:
        """Detect if a human is typing directly on the agent's WhatsApp.

        fromMe=True + fromApi=False = a real person using the device.
        """
        return message.is_from_agent and not message.is_from_api

    @classmethod
    def is_human_reply(cls, message: IncomingMessage, responsible_phones: list[str]) -> bool:
        """Detect if this is a human-in-the-loop reply (quoted reply from responsible).

        Handles Brazilian phone number variants (9-digit vs 8-digit after DDD).
        """
        has_reference = bool(message.reference_message_id)

        # Normalize Brazilian phone variants
        normalized = []
        for rp in responsible_phones:
            normalized.append(rp)
            if len(rp) == 13 and rp.startswith("55"):
                normalized.append(rp[:4] + rp[5:])

        return has_reference and message.sender_id in normalized

    @classmethod
    def _extract_text(cls, payload: dict) -> str:
        """Extract text content from various Z-API message types."""
        text = payload.get("text", {})
        if isinstance(text, dict):
            text = text.get("message", "")
        if isinstance(text, str) and text:
            return text.strip()

        image = payload.get("image", {})
        if isinstance(image, dict) and image.get("caption"):
            return image["caption"].strip()

        doc = payload.get("document", {})
        if isinstance(doc, dict) and doc.get("caption"):
            return doc["caption"].strip()

        if payload.get("audio"):
            return "[audio]"

        if payload.get("sticker"):
            return "[sticker]"

        return ""

    @classmethod
    def _extract_media_meta(cls, payload: dict) -> tuple[str | None, str | None, str | None]:
        """Extract media type, URL, and mimetype from Z-API payload."""
        audio = payload.get("audio", {})
        if isinstance(audio, dict) and audio.get("audioUrl"):
            return "audio", audio["audioUrl"], audio.get("mimeType", "audio/ogg")

        image = payload.get("image", {})
        if isinstance(image, dict) and image.get("imageUrl"):
            return "image", image["imageUrl"], image.get("mimeType", "image/jpeg")

        doc = payload.get("document", {})
        if isinstance(doc, dict) and doc.get("documentUrl"):
            mimetype = doc.get("mimeType", "application/octet-stream")
            media_type = "pdf" if "pdf" in mimetype.lower() else "document"
            return media_type, doc["documentUrl"], mimetype

        return None, None, None
```

---

- [ ] **Step 2.6: Create `core/channels/zapi/sender.py`**

This is a refactor of `core/messaging/zapi_send.py` to extend `ChannelSender`.

```python
"""
Aleph Framework — Z-API Message Sender
===============================================
Implements ChannelSender for WhatsApp via Z-API.

Sends messages with humanized delivery:
  - Splits response by paragraph into multiple messages
  - Random delay between messages (human-like typing)
  - Optional disclaimer at the end

Environment:
  ZAPI_INSTANCE      — Z-API instance ID
  ZAPI_TOKEN         — Z-API instance token
  ZAPI_CLIENT_TOKEN  — Z-API client token
  ZAPI_BASE_URL      — Z-API base URL (default: https://api.z-api.io/instances)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random

import httpx

from core.channels.base import ChannelSender
from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.channels.zapi")


class ZAPISender(ChannelSender):
    """Sends messages via Z-API with humanized delivery."""

    def __init__(self, config: FrameworkConfig):
        self.config = config
        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        base = os.environ.get("ZAPI_BASE_URL", "https://api.z-api.io/instances")
        instance = os.environ.get("ZAPI_INSTANCE", "")
        token = os.environ.get("ZAPI_TOKEN", "")
        return f"{base}/{instance}/token/{token}"

    @property
    def headers(self) -> dict:
        return {
            "Client-Token": os.environ.get("ZAPI_CLIENT_TOKEN", ""),
            "Content-Type": "application/json",
        }

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def send_response(self, recipient_id: str, text: str) -> None:
        """Send agent response with humanized delivery.

        Splits by paragraph, sends each as separate message with
        random delay. Appends disclaimer if enabled.
        """
        messaging = self.config.messaging

        if self.config.debug.dry_run:
            logger.info("[DRY RUN] Would send to %s: %s", recipient_id, text[:100])
            return

        parts = [p.strip() for p in text.split("\n") if p.strip()] if messaging.send_as_paragraphs else [text]

        if messaging.disclaimer.enabled:
            disclaimer = f"{messaging.disclaimer.separator}{messaging.disclaimer.text}"
            if parts:
                parts[-1] += disclaimer
            else:
                parts = [disclaimer]

        for i, part in enumerate(parts):
            await self._send_text(recipient_id, part)
            if i < len(parts) - 1:
                delay_ms = random.randint(messaging.delay_min_ms, messaging.delay_max_ms)
                await asyncio.sleep(delay_ms / 1000.0)

        logger.info("Sent %d message(s) to %s (%d chars total)", len(parts), recipient_id, len(text))

    async def send_notification(self, recipient_id: str, text: str) -> str | None:
        """Send a notification message. Returns the messageId for quote tracking."""
        if self.config.debug.dry_run:
            logger.info("[DRY RUN] Notification to %s: %s", recipient_id, text[:100])
            return None

        result = await self._send_text(recipient_id, text)
        if result:
            return result.get("messageId")
        return None

    async def _send_text(self, recipient_id: str, text: str) -> dict | None:
        """Send a single text message via Z-API."""
        url = f"{self.base_url}/send-text"
        payload = {"phone": recipient_id, "message": text}

        try:
            response = await self.http.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            logger.debug("Z-API sent to %s: %s", recipient_id, text[:50])
            return data
        except httpx.HTTPError as e:
            logger.error("Z-API send failed to %s: %s", recipient_id, str(e)[:200])
            return None
```

---

- [ ] **Step 2.7: Create `core/channels/zapi/__init__.py`**

```python
"""Aleph Framework — Z-API channel adapter."""

from __future__ import annotations

from core.channels.zapi.adapter import ZAPIAdapter
from core.channels.zapi.sender import ZAPISender

__all__ = ["ZAPIAdapter", "ZAPISender"]
```

---

- [ ] **Step 2.8: Run the channel tests**

```bash
python -m pytest tests/framework/test_channels.py -v
```

Expected: All tests PASSED

---

- [ ] **Step 2.9: Update `core/api/webhooks.py` imports and message access**

Replace the import block at the top (lines 32-41):

```python
# OLD (remove these):
from core.messaging.zapi_filter import (
    extract_message,
    should_filter,
    is_human_takeover_message,
    is_human_reply,
)
from core.messaging.zapi_send import ZAPISender

# NEW:
from core.channels.zapi.adapter import ZAPIAdapter
from core.channels.zapi.sender import ZAPISender
```

Update `webhook_zapi()` function — replace `extract_message` and `should_filter` calls, and update all `message["phone"]` / `message["text"]` / `message["message_id"]` dict access to attribute access:

The full updated function (replace the `webhook_zapi` function body):

```python
@app.post("/webhook/zapi")
async def webhook_zapi(request: Request):
    """Main Z-API webhook handler."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # Extract message via Z-API adapter
    message = ZAPIAdapter.extract(payload)
    if not message:
        return JSONResponse({"status": "ignored"})

    phone = message.sender_id
    text = message.text
    message_id = message.message_id

    # --- Filter ---
    filter_reason = ZAPIAdapter.should_filter(message, _registry.config)
    if filter_reason:
        logger.debug("Filtered [%s]: %s", filter_reason, phone)
        return JSONResponse({"status": "filtered", "reason": filter_reason})

    # --- Human reply detection (escalation response) ---
    if ZAPIAdapter.is_human_reply(message, _registry.config.human.responsible_phones):
        reference_id = message.reference_message_id
        if reference_id:
            logger.info("Human reply detected from %s (ref: %s)", phone, reference_id)
            asyncio.create_task(_handle_escalation_reply(phone, text, reference_id))
            return JSONResponse({"status": "escalation_reply_received"})

    # --- Takeover detection ---
    if ZAPIAdapter.is_human_takeover(message):
        raw_text = text.strip().upper()
        release_keyword = _registry.config.human.release_keyword

        if raw_text == release_keyword:
            await _redis.release_takeover(phone)
            logger.info("Takeover released via %s for %s", release_keyword, phone)
        else:
            await _redis.activate_takeover(phone)
            if _registry.config.human.takeover_renew_on_message:
                await _redis.renew_takeover(phone)
        return JSONResponse({"status": "takeover_handled"})

    # --- Check takeover active ---
    if await _redis.is_takeover_active(phone):
        logger.debug("Takeover active, ignoring message from %s", phone)
        return JSONResponse({"status": "takeover_active"})

    # --- Anti-spam ---
    if message_id and await _redis.is_duplicate(message_id):
        return JSONResponse({"status": "duplicate"})

    # --- Media processing (pre-buffer) ---
    if _registry.config.media.enabled and message.media_type:
        try:
            from core.media.processor import process_media
            processed = await process_media(
                {
                    "media_type": message.media_type,
                    "media_url": message.media_url,
                    "media_mimetype": message.media_mimetype,
                },
                _registry.config,
            )
            if processed:
                text = processed
            elif not text or text.startswith("["):
                logger.debug("Media message skipped (no processable content): %s", message.media_type)
                return JSONResponse({"status": "filtered", "reason": "media_unprocessable"})
        except Exception as e:
            logger.error("Media pre-processing error: %s", str(e)[:200])

    # --- Buffer chunked messages ---
    await _redis.buffer_message(phone, text)

    if phone in _buffer_timers:
        _buffer_timers[phone].cancel()

    _buffer_timers[phone] = asyncio.create_task(_process_after_buffer(phone))

    return JSONResponse({"status": "buffered"})
```

---

- [ ] **Step 2.10: Update type hints in `core/engine/pipeline.py`**

Add import at the top (after existing imports):

```python
from core.channels.base import ChannelSender
```

Change the `process_message` signature — update `sender=None` parameter type hint:

```python
async def process_message(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
    phone: str = "",
    redis_session=None,
    sender: ChannelSender | None = None,   # was: sender=None
    habits_db=None,
    knowledge_db=None,
    flow_engine=None,
    episodic_memory=None,
) -> PipelineResult:
```

---

- [ ] **Step 2.11: Update type hint in `core/human/escalation.py`**

Add import at the top (after existing imports):

```python
from core.channels.base import ChannelSender
```

Update `escalate_to_human` and `handle_human_response` signatures:

```python
async def escalate_to_human(
    redis_session,
    sender: ChannelSender,    # was: sender
    config,
    ...

async def handle_human_response(
    redis_session,
    sender: ChannelSender,    # was: sender
    registry,
    ...
```

---

- [ ] **Step 2.12: Delete `core/messaging/`**

```bash
rm core/messaging/zapi_filter.py
rm core/messaging/zapi_send.py
rm core/messaging/__init__.py
rmdir core/messaging/
```

---

- [ ] **Step 2.13: Run the full test suite**

```bash
python -m pytest tests/ -v
```

Expected: All tests PASSED (zero failures, zero import errors)

---

- [ ] **Step 2.14: Verify the app can boot without errors**

```bash
python -c "
from core.registry.loader import load_config
from core.registry.registry import AgentRegistry
from core.channels.zapi.adapter import ZAPIAdapter
from core.channels.zapi.sender import ZAPISender
from core.channels.base import IncomingMessage, ChannelSender
print('All imports OK')
"
```

Expected output: `All imports OK`

---

- [ ] **Step 2.15: Commit Task 2**

```bash
git add core/channels/ core/api/webhooks.py core/engine/pipeline.py core/human/escalation.py tests/framework/test_channels.py
git commit -m "feat(channels): channel abstraction layer — ZAPIAdapter + ChannelSender

Introduces core/channels/ with IncomingMessage dataclass and ChannelSender
ABC. Moves Z-API-specific logic into core/channels/zapi/ (adapter.py,
sender.py). Removes core/messaging/ entirely.

Adding a new channel (Telegram, SMS, etc.) now requires:
  - core/channels/<name>/adapter.py  — parse payload → IncomingMessage
  - core/channels/<name>/sender.py   — subclass ChannelSender
  - mount the route in core/api/webhooks.py"
```

---

## Final Steps

- [ ] **Step 3.1: Run the full test suite one last time**

```bash
python -m pytest tests/ -v
```

Expected: All tests PASSED

---

- [ ] **Step 3.2: Push branch and open PR**

```bash
git push -u origin feature/phase-1-llm-agnostic-channel-abstraction
gh pr create \
  --title "feat: Phase 1 — LLM agnosticism & channel abstraction" \
  --body "$(cat <<'EOF'
## Summary

- **Task 1 (LLM Agnosticism):** Created `core/llm/embeddings.py` with a unified, provider-agnostic embedding function. Both `core/habits/embeddings.py` and `core/knowledge/embeddings.py` now delegate to it instead of hardcoding Bifrost. Supports: Bifrost, OpenAI, Gemini, DeepSeek, OpenRouter, custom — same detection logic as `llm_router.py`.

- **Task 2 (Channel Abstraction):** Introduced `IncomingMessage` dataclass and `ChannelSender` ABC in `core/channels/base.py`. Moved Z-API logic into `core/channels/zapi/` as a concrete adapter. Removed `core/messaging/` entirely. The pipeline and escalation modules now type-annotate `sender` as `ChannelSender`.

## Test plan

- [ ] `python -m pytest tests/framework/test_embeddings.py -v` — all pass
- [ ] `python -m pytest tests/framework/test_channels.py -v` — all pass
- [ ] `python -m pytest tests/ -v` — zero regressions
- [ ] `aleph-agent test example` — config validates OK
- [ ] `python -c "from core.channels.zapi import ZAPIAdapter, ZAPISender; print('OK')"` — imports clean

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

---

## Self-Review

### Spec coverage
- ✅ Remove Bifrost hard-dependency from embeddings — Task 1
- ✅ Provider detection consistent with llm_router — Task 1
- ✅ IncomingMessage channel-agnostic type — Task 2
- ✅ ChannelSender ABC — Task 2
- ✅ Z-API as one concrete adapter — Task 2
- ✅ core/messaging/ removed — Task 2
- ✅ pipeline.py and escalation.py updated — Task 2
- ✅ App still works after refactor — Steps 2.13, 2.14

### Type consistency
- `ChannelSender` used in `pipeline.py:sender`, `escalation.py:sender`, and `ZAPISender` parent class
- `IncomingMessage.sender_id` used throughout `webhooks.py` (no mix of `phone` dict key and attribute)
- `_shared_generate` import alias used identically in both habits and knowledge embedding modules

### No placeholders
- All code blocks are complete and runnable
- All test assertions reference real attributes/methods defined in earlier tasks
