"""Zuper Agent Framework — LLM gateway (Bifrost)."""

from core.llm.bifrost import (
    create_primary_model,
    create_fallback_model,
    create_model_settings,
)

__all__ = ["create_primary_model", "create_fallback_model", "create_model_settings"]
