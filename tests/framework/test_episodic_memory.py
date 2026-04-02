"""Tests: Phase 12 — Episodic Session Memory schema and behavior."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from core.registry.schema import FrameworkConfig


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
