"""Tests: Phase 12 — Episodic Session Memory schema and behavior."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.registry.schema import FrameworkConfig, SDKSessionsConfig


def _make_config(**session_overrides) -> FrameworkConfig:
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
