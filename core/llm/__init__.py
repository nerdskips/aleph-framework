"""Aleph Framework — LLM gateway (provider-agnostic router)."""

from __future__ import annotations

from core.llm.llm_router import (
    create_primary_model,
    create_fallback_model,
    create_model_settings,
)

__all__ = ["create_primary_model", "create_fallback_model", "create_model_settings"]
