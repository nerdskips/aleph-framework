"""Tests: Phase 12 — Episodic Session Memory schema and behavior."""

from __future__ import annotations

import time
import uuid

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
