"""Tests: Phase 14 — Self-Awareness context injection."""

from __future__ import annotations

from core.registry.schema import FrameworkConfig


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
