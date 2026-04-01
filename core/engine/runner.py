"""
Aleph Framework — Agent Runner
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
import os
import time
import zoneinfo
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from agents import Agent, ModelSettings, Runner

from core.llm.bifrost import (
    create_fallback_model,
    create_model_settings,
    create_primary_model,
)
from core.registry.registry import AgentRegistry
from core.registry.schema import FrameworkConfig, SubAgentConfig

logger = logging.getLogger("aleph.engine")


# ---------------------------------------------------------------------------
# Sub-agent builder (D2 — MANAGER pattern)
# ---------------------------------------------------------------------------

def _build_sub_agent(sub: SubAgentConfig, config: FrameworkConfig, model: Any) -> Agent:
    """Build a specialist sub-agent from SubAgentConfig.

    The sub-agent runs its own Agent+Runner loop when invoked as a tool.
    Model: uses sub.model if set, otherwise inherits the main agent's model.
    Tools: built from sub.tools list (same webhook/code factory as main agent).
    """
    from core.registry.tool_loader import build_tools_from_config  # lazy import

    sub_model = model  # inherit by default
    if sub.ref:
        # Ref mode: load instructions from another agent dir's system prompt
        ref_path = config.client_dir.parent / sub.ref if not sub.ref.startswith("/") else sub.ref  # type: ignore[arg-type]
        try:
            prompt_path = ref_path / "prompts" / "system.md"
            instructions = prompt_path.read_text() if prompt_path.is_file() else sub.instructions
        except Exception:
            instructions = sub.instructions
    else:
        instructions = sub.instructions

    sub_tools = build_tools_from_config(sub.tools, config.client_dir) if sub.tools else []

    return Agent(
        name=sub.name,
        instructions=instructions,
        model=sub_model,
        tools=sub_tools,
    )


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
        Configured SDK Agent ready to run. Sub-agents (if configured) are
        added as tools via as_tool() so the orchestrator can invoke them.
    """
    config = registry.config

    # Inject current datetime if TZ is set
    instructions = registry.system_prompt
    tz = os.environ.get("TZ")
    if tz:
        try:
            now = datetime.now(zoneinfo.ZoneInfo(tz))
            timestamp = now.strftime("%A, %d/%m/%Y, %H:%M")
            instructions = f"[Data e horário atual: {timestamp} ({tz})]\n\n{instructions}"
        except Exception:
            pass  # Invalid TZ, skip silently

    # Start with main agent tools
    all_tools = list(registry.tools)

    # D2 — add sub-agents as tools (MANAGER pattern)
    for sub in config.subagents:
        try:
            sub_agent = _build_sub_agent(sub, config, model)
            sub_tool = sub_agent.as_tool(
                tool_name=sub.tool_name,
                tool_description=sub.tool_description,
                max_turns=sub.max_turns,
            )
            all_tools.append(sub_tool)
            logger.info("Sub-agent registered as tool: %s", sub.tool_name)
        except Exception as e:
            logger.warning("Failed to build sub-agent '%s': %s", sub.name, e)

    agent = Agent(
        name=registry.agent_name,
        instructions=instructions,
        model=model,
        model_settings=model_settings,
        tools=all_tools,
    )

    logger.info(
        "Agent built: name=%s tools=%d (sub-agents=%d) prompt=%d chars",
        registry.agent_name,
        len(all_tools),
        len(config.subagents),
        len(registry.system_prompt),
    )

    return agent


# ---------------------------------------------------------------------------
# Agent result
# ---------------------------------------------------------------------------

@dataclass
class AgentResult:
    """Result from running the agent."""
    response: str
    tool_calls: list[str] = field(default_factory=list)  # names of tools called


def _extract_tool_calls(result: Any) -> list[str]:
    """Extract tool call names from SDK RunResult."""
    tool_names = []
    try:
        for item in result.new_items:
            # SDK 0.12: ToolCallItem has a name attribute
            item_type = type(item).__name__
            if "ToolCall" in item_type or "tool_call" in item_type.lower():
                name = getattr(item, "name", None) or getattr(item, "tool_name", "")
                if name:
                    tool_names.append(name)
            # Also check raw_item for tool calls
            raw = getattr(item, "raw_item", None)
            if raw and hasattr(raw, "name"):
                if raw.name and raw.name not in tool_names:
                    tool_names.append(raw.name)
    except Exception:
        pass  # Don't crash on introspection failure
    return tool_names


# ---------------------------------------------------------------------------
# Execution with fallback
# ---------------------------------------------------------------------------

async def run_agent(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
) -> AgentResult:
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
        AgentResult with response text and list of tool calls made
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
            max_turns=config.sdk.handoffs.max_turns,
        )

        elapsed = time.monotonic() - start
        response = result.final_output
        tool_calls = _extract_tool_calls(result)

        logger.info(
            "Primary model responded: %d chars in %.1fs, tools=%s",
            len(response) if response else 0,
            elapsed,
            tool_calls or "(none)",
        )

        return AgentResult(response=response, tool_calls=tool_calls)

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
            max_turns=config.sdk.handoffs.max_turns,
        )

        elapsed = time.monotonic() - start
        response = result.final_output
        tool_calls = _extract_tool_calls(result)

        logger.info(
            "Fallback model responded: %d chars in %.1fs, tools=%s",
            len(response) if response else 0,
            elapsed,
            tool_calls or "(none)",
        )

        return AgentResult(response=response, tool_calls=tool_calls)

    except Exception as e:
        logger.error(
            "Both models failed. Primary: %s, Fallback: %s. Error: %s",
            config.agent.model,
            config.agent.fallback_model,
            str(e)[:300],
        )
        return AgentResult(
            response=(
                "Desculpe, estou com dificuldades técnicas no momento. "
                "Por favor, tente novamente em alguns instantes."
            ),
            tool_calls=[],
        )


def run_agent_sync(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
) -> AgentResult:
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
        description="Aleph Framework — Terminal Runner"
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
        # Interactive chat mode with full pipeline (guardrails + agent)
        from core.engine.pipeline import process_message

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

            result = asyncio.run(process_message(registry, user_input, history))

            # Show guardrail info if matched
            if result.input_classification and result.input_classification.matched:
                cls = result.input_classification
                print(f"  🛡️ Guardrail: {cls.pattern_name} → {cls.action.value}")

            if result.output_check and result.output_check.blocked:
                print(f"  🚫 Output blocked: {result.output_check.rule_name}")

            if result.skipped_llm:
                print("  ⚡ LLM skipped (guardrail handled)")

            print(f"\n🤖 {registry.agent_name}: {result.response}")
            print(f"  ⏱️ {result.elapsed_seconds:.1f}s")

            # Append to history for multi-turn
            history.append({"role": "user", "content": user_input})
            history.append({"role": "assistant", "content": result.response})

    elif args.message:
        # Single message mode with full pipeline
        from core.engine.pipeline import process_message

        result = asyncio.run(process_message(registry, args.message))

        if result.input_classification and result.input_classification.matched:
            cls = result.input_classification
            print(f"  🛡️ Guardrail: {cls.pattern_name} → {cls.action.value}")

        print(f"\n🤖 {registry.agent_name}: {result.response}")
        print(f"  ⏱️ {result.elapsed_seconds:.1f}s")

    else:
        print("Provide --message or --interactive", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
