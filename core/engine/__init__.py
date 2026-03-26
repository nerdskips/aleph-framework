"""Aleph Framework — Execution engine."""

from core.engine.runner import run_agent, run_agent_sync, build_agent
from core.engine.pipeline import process_message, PipelineResult

__all__ = ["run_agent", "run_agent_sync", "build_agent", "process_message", "PipelineResult"]
