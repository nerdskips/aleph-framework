"""Tests: Phase 14 — Self-Awareness context injection."""

from __future__ import annotations

import time

from core.awareness.injector import build_injection, should_inject
from core.awareness.reader import AwarenessState, build_awareness_state
from core.registry.schema import FrameworkConfig
from core.session.memory import MemoryContext


def _make_config(**awareness_overrides) -> FrameworkConfig:
    return FrameworkConfig(
        client_id="test",
        agent={"name": "Bot", "model": "gpt-4o-mini"},
        self_awareness=awareness_overrides,
    )


def test_self_awareness_default_off():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.self_awareness.enabled is False


def test_self_awareness_default_return_gap():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.self_awareness.return_gap_minutes == 30


def test_self_awareness_default_max_age():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.self_awareness.max_injection_age_hours == 4.0


def test_self_awareness_can_be_enabled():
    config = _make_config(enabled=True)
    assert config.self_awareness.enabled is True


def _mock_memory_ctx(summary="", last_ts=0.0):
    return MemoryContext(raw_history=[], summary=summary, last_turn_ts=last_ts)


async def test_no_injection_for_new_user():
    config = _make_config(enabled=True)
    state = await build_awareness_state(
        config=config,
        memory_ctx=_mock_memory_ctx(),
        flow_state=None,
        escalation=None,
    )
    assert should_inject(config.self_awareness, state) is False


async def test_no_injection_when_recent_user():
    config = _make_config(enabled=True, return_gap_minutes=30)
    ctx = _mock_memory_ctx(summary="some context", last_ts=time.time() - 5 * 60)
    state = await build_awareness_state(config=config, memory_ctx=ctx, flow_state=None, escalation=None)
    assert should_inject(config.self_awareness, state) is False


async def test_injection_fires_after_gap():
    config = _make_config(enabled=True, return_gap_minutes=30)
    ctx = _mock_memory_ctx(summary="User wants delivery on Saturday", last_ts=time.time() - 45 * 60)
    state = await build_awareness_state(config=config, memory_ctx=ctx, flow_state=None, escalation=None)
    assert should_inject(config.self_awareness, state) is True


async def test_no_injection_when_state_too_old():
    config = _make_config(enabled=True, return_gap_minutes=30, max_injection_age_hours=4)
    ctx = _mock_memory_ctx(summary="old context", last_ts=time.time() - 6 * 3600)
    state = await build_awareness_state(config=config, memory_ctx=ctx, flow_state=None, escalation=None)
    assert should_inject(config.self_awareness, state) is False


def test_build_injection_includes_summary():
    config = _make_config(enabled=True)
    state = AwarenessState(
        summary="User wants delivery on Saturday",
        flow_id=None,
        flow_step=None,
        escalation_active=False,
        elapsed_minutes=45.0,
    )
    text = build_injection(config.self_awareness, state)
    assert "Saturday" in text
    assert text.strip() != ""


def test_build_injection_includes_flow():
    config = _make_config(enabled=True)
    state = AwarenessState(
        summary="",
        flow_id="checkout",
        flow_step="waiting_address",
        escalation_active=False,
        elapsed_minutes=45.0,
    )
    text = build_injection(config.self_awareness, state)
    assert "checkout" in text
    assert "waiting_address" in text
