"""
Aleph Framework — Flows module
=====================================
Declarative multi-step conversation flows (state machine).
Triggered by keywords/regex, advances step-by-step collecting user replies.

Usage: enable via YAML (DEFAULT OFF)
  flows:
    enabled: true
    flows:
      - id: onboarding
        ...
"""

from __future__ import annotations

from core.flows.engine import FlowEngine, FlowResolution
from core.flows.state import FlowState

__all__ = ["FlowEngine", "FlowResolution", "FlowState"]
