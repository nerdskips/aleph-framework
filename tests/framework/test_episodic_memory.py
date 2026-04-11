"""Tests: Phase 12 — Episodic Session Memory schema and behavior."""

from __future__ import annotations

import time
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from core.registry.schema import FrameworkConfig
from core.session.memory import EpisodicMemory, MemoryContext


def _make_config(**session_overrides) -> FrameworkConfig:
    # max_raw_turns=4 intentionally set below default (8) for faster compression in tests
    sdk = {"sessions": {"max_raw_turns": 4, **session_overrides}}
    return FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"}, sdk=sdk)


def test_sessions_default_max_raw_turns():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.max_raw_turns == 8

def test_sessions_default_compression_model_empty():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.compression_model == ""

def test_sessions_default_gap_compression_hours():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.gap_compression_hours == 3.0

def test_sessions_default_summary_ttl_days():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.summary_ttl_days == 30

def test_sessions_custom_values():
    config = _make_config(max_raw_turns=6, gap_compression_hours=1.5, summary_ttl_days=7)
    assert config.sdk.sessions.max_raw_turns == 6
    assert config.sdk.sessions.gap_compression_hours == 1.5
    assert config.sdk.sessions.summary_ttl_days == 7


def test_max_raw_turns_rejects_below_minimum():
    with pytest.raises(ValidationError):
        FrameworkConfig(
            client_id="test",
            agent={"name": "Bot", "model": "gpt-4o-mini"},
            sdk={"sessions": {"max_raw_turns": 1}},
        )


def test_gap_compression_hours_rejects_below_minimum():
    with pytest.raises(ValidationError):
        FrameworkConfig(
            client_id="test",
            agent={"name": "Bot", "model": "gpt-4o-mini"},
            sdk={"sessions": {"gap_compression_hours": 0.1}},
        )


def _make_memory(max_raw_turns: int = 4) -> EpisodicMemory:
    # Use a unique client_id per call to avoid cross-test state in _MEMORY_STORE
    unique_id = uuid.uuid4().hex[:8]
    sdk = {"sessions": {"max_raw_turns": max_raw_turns}}
    config = FrameworkConfig(client_id=f"test_{unique_id}", agent={"name": "Bot", "model": "gpt-4o-mini"}, sdk=sdk)
    return EpisodicMemory(config, redis_client=None)  # in-memory mode


async def test_get_context_empty_user():
    mem = _make_memory()
    ctx = await mem.get_context("+5511999")
    assert isinstance(ctx, MemoryContext)
    assert ctx.raw_history == []
    assert ctx.summary == ""
    assert ctx.last_turn_ts == 0.0


async def test_save_turn_adds_two_messages():
    mem = _make_memory()
    await mem.save_turn("+5511999", "oi", "olá")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) == 2
    assert ctx.raw_history[0] == {"role": "user", "content": "oi"}
    assert ctx.raw_history[1] == {"role": "assistant", "content": "olá"}


async def test_multiple_turns_accumulate():
    mem = _make_memory(max_raw_turns=8)
    await mem.save_turn("+5511999", "msg1", "resp1")
    await mem.save_turn("+5511999", "msg2", "resp2")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) == 4


async def test_last_turn_ts_updated():
    mem = _make_memory()
    before = time.time()
    await mem.save_turn("+5511999", "oi", "olá")
    ctx = await mem.get_context("+5511999")
    assert ctx.last_turn_ts >= before


async def test_different_phones_isolated():
    mem = _make_memory()
    await mem.save_turn("+5511111", "a", "b")
    await mem.save_turn("+5522222", "c", "d")
    ctx1 = await mem.get_context("+5511111")
    ctx2 = await mem.get_context("+5522222")
    assert ctx1.raw_history[0]["content"] == "a"
    assert ctx2.raw_history[0]["content"] == "c"


async def test_in_memory_window_capped_at_max_turns():
    """Window must not grow beyond max_raw_turns * 2 messages (2 messages per turn)."""
    mem = _make_memory(max_raw_turns=2)  # window = 4 messages max
    # Fill window
    await mem.save_turn("+5511999", "msg1", "resp1")
    await mem.save_turn("+5511999", "msg2", "resp2")
    # Overflow: adding a 3rd turn should cap the window
    await mem.save_turn("+5511999", "msg3", "resp3")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) <= 4  # max_raw_turns * 2
    # Newest messages must be retained
    contents = [m["content"] for m in ctx.raw_history]
    assert "msg3" in contents
    assert "resp3" in contents


def _make_redis_mock() -> AsyncMock:
    """Build a fake Redis client backed by a plain dict."""
    redis = AsyncMock()
    _store: dict[str, str] = {}

    async def mock_get(key):
        return _store.get(key)

    async def mock_set(key, value, ex=None):
        _store[key] = value

    async def mock_delete(key):
        _store.pop(key, None)

    redis.get = mock_get
    redis.set = mock_set
    redis.delete = mock_delete
    return redis


def _make_redis_memory(max_raw_turns: int = 4) -> EpisodicMemory:
    config = _make_config(max_raw_turns=max_raw_turns)
    return EpisodicMemory(config, redis_client=_make_redis_mock())


async def test_redis_get_context_empty():
    mem = _make_redis_memory()
    ctx = await mem.get_context("+5511999")
    assert ctx.raw_history == []
    assert ctx.summary == ""


async def test_redis_save_and_retrieve_turn():
    mem = _make_redis_memory()
    await mem.save_turn("+5511999", "oi", "olá")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) == 2
    assert ctx.raw_history[0]["role"] == "user"
    assert ctx.raw_history[1]["role"] == "assistant"


async def test_redis_window_triggers_compression():
    """When turns exceed max_raw_turns, oldest half is removed from raw history."""
    mem = _make_redis_memory(max_raw_turns=2)  # window = 2 turns = 4 messages
    # Fill window exactly
    await mem.save_turn("+5511999", "msg1", "resp1")
    await mem.save_turn("+5511999", "msg2", "resp2")
    # This turn overflows — triggers compression of oldest 2 messages
    with patch.object(mem, "_compress_messages", new_callable=AsyncMock) as mock_compress:
        await mem.save_turn("+5511999", "msg3", "resp3")
        mock_compress.assert_called_once()

    ctx = await mem.get_context("+5511999")
    # After compression, raw history should have at most max_raw_turns * 2 messages
    assert len(ctx.raw_history) <= 4


async def test_gap_compression_triggers_when_old():
    mem = _make_redis_memory()
    # Simulate last turn 4 hours ago
    mem.redis.get = AsyncMock(return_value=str(time.time() - 4 * 3600))
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_called_once_with("+5511999", deep=True)


async def test_gap_compression_skips_when_recent():
    mem = _make_redis_memory()
    mem.redis.get = AsyncMock(return_value=str(time.time() - 30 * 60))  # 30 min ago
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_not_called()


async def test_gap_compression_skips_new_user():
    mem = _make_redis_memory()
    mem.redis.get = AsyncMock(return_value=None)  # new user
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_not_called()


async def test_gap_compression_noop_in_memory_mode():
    """check_gap_compression is a no-op when no Redis client is configured."""
    mem = _make_memory()  # in-memory mode
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_not_called()
