"""Aleph Framework — Execution engine."""

from core.engine.pipeline import PipelineResult, process_message
from core.engine.runner import build_agent, run_agent, run_agent_sync

__all__ = ["run_agent", "run_agent_sync", "build_agent", "process_message", "PipelineResult"]
