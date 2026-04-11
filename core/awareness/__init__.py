"""Aleph Framework — Self-Awareness context injection (Phase 14)."""

from __future__ import annotations

from core.awareness.injector import build_injection, should_inject
from core.awareness.reader import AwarenessState, build_awareness_state

__all__ = ["AwarenessState", "build_awareness_state", "should_inject", "build_injection"]
