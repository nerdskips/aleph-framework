"""
Zuper Agent Framework — Agent Runner
======================================
Builds an OpenAI Agents SDK Agent from the Registry and executes it.

Responsibilities:
  - Create SDK Agent with tools, prompt, and model from registry
  - Run with primary model, fallback to secondary on failure
  - Return the agent's text response
  - CLI mode for terminal testing (no WhatsApp needed)

Usage (terminal test):
  python -m core.engine.runner --client example --message "oi, testa o echo"

This is the EXECUTION layer. It receives a fully loaded AgentRegistry
and produces a response string. It does NOT handle:
  - WhatsApp I/O (that's messaging/)
  - Guardrails (that's guardrails/ — will be wired in pipeline.py)
  - Session/buffer (that's session/)
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from dotenv import load_dotenv; load_dotenv()

from agents import Agent, Runner, ModelSettings

from core.registry.registry import AgentRegistry
from core.registry.schema import FrameworkConfig
from core.llm.bifrost import (
    create_primary_model,
    create_fallback_model,
    create_model_settings,
)

logger = logging.getLogger("zuper.engine")


# ---------------------------------------------------------------------------
# Agent builder
# ---------------------------------------------------------------------------

def build_agent(
    registry: AgentRegistry,
    model: Any,
    model_settings: ModelSettings,
) -> Agent:
    """Build an SDK Agent from the registry.

    Args:
        registry: Loaded AgentRegistry with config, tools, prompt
        model: OpenAIChatCompletionsModel instance (primary or fallback)
        model_settings: Temperature, max_tokens, etc.

    Returns:
        Configured SDK Agent ready to run
    """
    agent = Agent(
        name=registry.agent_name,
        instructions=registry.system_prompt,
        model=model,
        model_settings=model_settings,
        tools=registry.tools,
    )

    logger.info(
        "Agent built: name=%s tools=%d prompt=%d chars",
        registry.agent_name,
        len(registry.tools),
        len(registry.system_prompt),
    )

    return agent


# ---------------------------------------------------------------------------
# Execution with fallback
# ---------------------------------------------------------------------------

async def run_agent(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
) -> str:
    """Run the agent with automatic fallback.

    Flow:
      1. Try primary model
      2. If primary fails → try fallback model
      3. If both fail → return error message

    Args:
        registry: Loaded AgentRegistry
        user_message: The user's text input
        message_history: Optional conversation history for context

    Returns:
        Agent's text response
    """
    config = registry.config
    model_settings = create_model_settings(config)

    # Build input
    input_messages = []
    if message_history:
        input_messages.extend(message_history)
    input_messages.append({"role": "user", "content": user_message})

    # --- Try primary model ---
    try:
        primary_model = create_primary_model(config)
        agent = build_agent(registry, primary_model, model_settings)

        logger.info(
            "Running agent with primary model: %s",
            config.agent.model,
        )
        start = time.monotonic()

        result = await Runner.run(
            agent,
            input=input_messages,
        )

        elapsed = time.monotonic() - start
        response = result.final_output

        logger.info(
            "Primary model responded: %d chars in %.1fs",
            len(response) if response else 0,
            elapsed,
        )

        return response

    except Exception as e:
        logger.warning(
            "Primary model failed (%s: %s), trying fallback...",
            type(e).__name__,
            str(e)[:200],
        )

    # --- Try fallback model ---
    try:
        fallback_model = create_fallback_model(config)
        agent = build_agent(registry, fallback_model, model_settings)

        logger.info(
            "Running agent with fallback model: %s",
            config.agent.fallback_model,
        )
        start = time.monotonic()

        result = await Runner.run(
            agent,
            input=input_messages,
        )

        elapsed = time.monotonic() - start
        response = result.final_output

        logger.info(
            "Fallback model responded: %d chars in %.1fs",
            len(response) if response else 0,
            elapsed,
        )

        return response

    except Exception as e:
        logger.error(
            "Both models failed. Primary: %s, Fallback: %s. Error: %s",
            config.agent.model,
            config.agent.fallback_model,
            str(e)[:300],
        )
        return (
            "Desculpe, estou com dificuldades técnicas no momento. "
            "Por favor, tente novamente em alguns instantes."
        )


def run_agent_sync(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
) -> str:
    """Synchronous wrapper for run_agent (convenience for scripts/CLI)."""
    return asyncio.run(run_agent(registry, user_message, message_history))


# ---------------------------------------------------------------------------
# CLI entrypoint for terminal testing
# ---------------------------------------------------------------------------

def main():
    """CLI: python -m core.engine.runner --client example --message 'oi'

    Tests the full flow: YAML → Registry → Agent → LLM → Response
    No WhatsApp, no Redis, no Z-API needed.
    """
    import argparse
    import sys

    parser = argparse.ArgumentParser(
        description="Zuper Agent Framework — Terminal Runner"
    )
    parser.add_argument("--client", type=str, required=True, help="Client ID")
    parser.add_argument("--message", type=str, help="Single message to send")
    parser.add_argument("--interactive", action="store_true", help="Interactive chat mode")
    parser.add_argument(
        "--log-level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log level",
    )
    args = parser.parse_args()

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Boot registry
    try:
        registry = AgentRegistry.from_config(client_id=args.client)
        print(registry.summary())
    except Exception as e:
        print(f"❌ Boot failed: {e}", file=sys.stderr)
        sys.exit(1)

    if args.interactive:
        # Interactive chat mode
        print("\n💬 Interactive mode — type 'quit' to exit")
        print("-" * 40)
        history = []

        while True:
            try:
                user_input = input("\n👤 You: ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\n👋 Bye!")
                break

            if user_input.lower() in ("quit", "exit", "q"):
                print("👋 Bye!")
                break

            if not user_input:
                continue

            response = run_agent_sync(registry, user_input, history)
            print(f"\n🤖 {registry.agent_name}: {response}")

            # Append to history for multi-turn
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": response})

    elif args.message:
        # Single message mode
        response = run_agent_sync(registry, args.message)
        print(f"\n🤖 {registry.agent_name}: {response}")

    else:
        print("Provide --message or --interactive", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
