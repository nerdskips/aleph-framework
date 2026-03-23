"""
Zuper Agent Framework — Bifrost LLM Client
============================================
Creates OpenAI-compatible model instances pointing at the Bifrost
gateway (or any OpenAI-compatible endpoint).

Handles:
  - Primary model with configured timeout
  - Fallback model with separate (longer) timeout
  - Environment variable override for gateway URL and API key
  - Works with Bifrost, OpenAI direct, OpenRouter, LiteLLM, etc.

The junior never touches this — it reads everything from config + env.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from openai import AsyncOpenAI

from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from agents import ModelSettings

from core.registry.schema import FrameworkConfig

logger = logging.getLogger("zuper.llm")


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _create_openai_client(
    gateway_url: str,
    api_key: str,
    timeout: int,
) -> AsyncOpenAI:
    """Create an AsyncOpenAI client pointing at the gateway.

    Supports env var override:
      BIFROST_URL  → overrides config gateway_url
      BIFROST_API_KEY → overrides config api_key
    """
    url = os.environ.get("BIFROST_URL", gateway_url)
    key = os.environ.get("BIFROST_API_KEY", api_key)

    return AsyncOpenAI(
        base_url=url,
        api_key=key,
        timeout=float(timeout),
    )


# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------

def create_primary_model(config: FrameworkConfig) -> OpenAIChatCompletionsModel:
    """Create the primary LLM model from config.

    Returns an OpenAIChatCompletionsModel that the SDK Agent can use directly.
    Points at Bifrost with the model ID from config.agent.model.
    """
    client = _create_openai_client(
        gateway_url=config.llm.gateway_url,
        api_key=config.llm.api_key,
        timeout=config.llm.timeout_seconds,
    )

    model = OpenAIChatCompletionsModel(
        model=config.agent.model,
        openai_client=client,
    )

    logger.info(
        "Primary model created: %s via %s (timeout: %ds)",
        config.agent.model,
        os.environ.get("BIFROST_URL", config.llm.gateway_url),
        config.llm.timeout_seconds,
    )

    return model


def create_fallback_model(config: FrameworkConfig) -> OpenAIChatCompletionsModel:
    """Create the fallback LLM model from config.

    Uses the same gateway but with:
      - config.agent.fallback_model as the model ID
      - config.llm.fallback_timeout_seconds as timeout (longer for slow models)
    """
    client = _create_openai_client(
        gateway_url=config.llm.gateway_url,
        api_key=config.llm.api_key,
        timeout=config.llm.fallback_timeout_seconds,
    )

    model = OpenAIChatCompletionsModel(
        model=config.agent.fallback_model,
        openai_client=client,
    )

    logger.info(
        "Fallback model created: %s via %s (timeout: %ds)",
        config.agent.fallback_model,
        os.environ.get("BIFROST_URL", config.llm.gateway_url),
        config.llm.fallback_timeout_seconds,
    )

    return model


def create_model_settings(config: FrameworkConfig) -> ModelSettings:
    """Create ModelSettings from config for the SDK Agent."""
    return ModelSettings(
        temperature=config.agent.temperature,
        max_tokens=config.agent.max_tokens,
    )
