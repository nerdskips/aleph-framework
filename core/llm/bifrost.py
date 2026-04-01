"""
Aleph Framework — LLM Client
=====================================
Creates OpenAI-compatible model instances for the SDK Agent.

Supports two modes:
  1. Bifrost gateway (default) — local proxy that routes to multiple providers
  2. Direct API key — connects straight to OpenAI, Gemini, DeepSeek, etc.

Mode is determined by LLM_PROVIDER env var (or config.llm.provider):
  - "bifrost" (default): uses BIFROST_URL + BIFROST_API_KEY
  - "openai":  uses https://api.openai.com/v1 + OPENAI_API_KEY
  - "gemini":  uses https://generativelanguage.googleapis.com/v1beta/openai + GEMINI_API_KEY
  - "deepseek": uses https://api.deepseek.com/v1 + DEEPSEEK_API_KEY
  - "openrouter": uses https://openrouter.ai/api/v1 + OPENROUTER_API_KEY
  - "custom":  uses LLM_BASE_URL + LLM_API_KEY (any OpenAI-compatible endpoint)

The junior configures this in the agent's .env — never touches Python.
"""

from __future__ import annotations

import logging
import os

from agents import ModelSettings
from agents.models.openai_chatcompletions import OpenAIChatCompletionsModel
from openai import AsyncOpenAI

from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.llm")


# ---------------------------------------------------------------------------
# Provider registry — base URLs and env var names
# ---------------------------------------------------------------------------

PROVIDERS = {
    "bifrost": {
        "base_url": "http://localhost:8080/v1",
        "env_url": "BIFROST_URL",
        "env_key": "BIFROST_API_KEY",
        "default_key": "dummy",
    },
    "openai": {
        "base_url": "https://api.openai.com/v1",
        "env_url": None,  # fixed URL
        "env_key": "OPENAI_API_KEY",
        "default_key": "",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "env_url": None,
        "env_key": "GEMINI_API_KEY",
        "default_key": "",
    },
    "deepseek": {
        "base_url": "https://api.deepseek.com/v1",
        "env_url": None,
        "env_key": "DEEPSEEK_API_KEY",
        "default_key": "",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "env_url": None,
        "env_key": "OPENROUTER_API_KEY",
        "default_key": "",
    },
    "custom": {
        "base_url": "",
        "env_url": "LLM_BASE_URL",
        "env_key": "LLM_API_KEY",
        "default_key": "",
    },
}


# ---------------------------------------------------------------------------
# Provider resolution
# ---------------------------------------------------------------------------

def _resolve_provider(config: FrameworkConfig) -> str:
    """Determine the LLM provider.

    Priority:
      1. LLM_PROVIDER env var
      2. config.llm.provider from YAML
      3. Auto-detect from env vars (if OPENAI_API_KEY set → openai, etc)
      4. Default: "bifrost"
    """
    # 1. Env var override
    env_provider = os.environ.get("LLM_PROVIDER", "").lower().strip()
    if env_provider and env_provider in PROVIDERS:
        return env_provider

    # 2. Config value
    config_provider = getattr(config.llm, "provider", "").lower().strip()
    if config_provider and config_provider in PROVIDERS:
        return config_provider

    # 3. Auto-detect from env vars
    if os.environ.get("OPENAI_API_KEY"):
        return "openai"
    if os.environ.get("GEMINI_API_KEY"):
        return "gemini"
    if os.environ.get("DEEPSEEK_API_KEY"):
        return "deepseek"
    if os.environ.get("OPENROUTER_API_KEY"):
        return "openrouter"
    if os.environ.get("LLM_BASE_URL") and os.environ.get("LLM_API_KEY"):
        return "custom"

    # 4. Default
    return "bifrost"


# ---------------------------------------------------------------------------
# Client factory
# ---------------------------------------------------------------------------

def _create_openai_client(
    config: FrameworkConfig,
    timeout: int,
) -> AsyncOpenAI:
    """Create an AsyncOpenAI client based on the resolved provider."""
    provider_name = _resolve_provider(config)
    provider = PROVIDERS[provider_name]

    # Resolve base URL
    if provider["env_url"]:
        base_url = os.environ.get(provider["env_url"], "") or provider["base_url"]
    else:
        base_url = provider["base_url"]

    # Resolve API key
    api_key = os.environ.get(provider["env_key"], "") or provider["default_key"]

    if not api_key and provider_name != "bifrost":
        logger.warning(
            "LLM provider '%s' selected but %s not set in .env",
            provider_name, provider["env_key"],
        )

    if not base_url:
        raise ValueError(
            f"LLM provider '{provider_name}' requires {provider['env_url']} in .env"
        )

    logger.info(
        "LLM client: provider=%s, url=%s, timeout=%ds",
        provider_name, base_url, timeout,
    )

    return AsyncOpenAI(
        base_url=base_url,
        api_key=api_key,
        timeout=float(timeout),
    )


# ---------------------------------------------------------------------------
# Model factory (public API — unchanged interface)
# ---------------------------------------------------------------------------

def create_primary_model(config: FrameworkConfig) -> OpenAIChatCompletionsModel:
    """Create the primary LLM model from config.

    Works with any provider — Bifrost, OpenAI direct, Gemini, etc.
    The model ID from config.agent.model is passed through as-is.
    """
    client = _create_openai_client(config, config.llm.timeout_seconds)

    model = OpenAIChatCompletionsModel(
        model=config.agent.model,
        openai_client=client,
    )

    logger.info("Primary model created: %s", config.agent.model)
    return model


def create_fallback_model(config: FrameworkConfig) -> OpenAIChatCompletionsModel:
    """Create the fallback LLM model from config.

    Uses the same provider but with:
      - config.agent.fallback_model as the model ID
      - config.llm.fallback_timeout_seconds as timeout
    """
    client = _create_openai_client(config, config.llm.fallback_timeout_seconds)

    model = OpenAIChatCompletionsModel(
        model=config.agent.fallback_model,
        openai_client=client,
    )

    logger.info("Fallback model created: %s", config.agent.fallback_model)
    return model


def create_model_settings(config: FrameworkConfig) -> ModelSettings:
    """Create ModelSettings from config for the SDK Agent."""
    return ModelSettings(
        temperature=config.agent.temperature,
        max_tokens=config.agent.max_tokens,
        parallel_tool_calls=config.agent.parallel_tool_calls,
    )
