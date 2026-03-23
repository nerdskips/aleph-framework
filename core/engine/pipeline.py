"""
Zuper Agent Framework — Execution Pipeline
=============================================
The complete message processing flow:

  input text
    → input guardrail (deterministic, pre-LLM)
    → if redirect/block: return immediately (zero LLM cost)
    → run agent (LLM via Bifrost, with fallback)
    → output guardrail (post-LLM validation)
    → if blocked: return safe response
    → return agent response

This is what webhooks.py calls. It replaces direct run_agent calls.
"""

from __future__ import annotations

import logging
import time

from core.registry.registry import AgentRegistry
from core.registry.schema import GuardrailAction
from core.guardrails.input import classify_input, ClassificationResult
from core.guardrails.output import check_output, OutputGuardrailResult
from core.engine.runner import run_agent

logger = logging.getLogger("zuper.pipeline")


# ---------------------------------------------------------------------------
# Pipeline result
# ---------------------------------------------------------------------------

class PipelineResult:
    """Result of the full pipeline execution."""

    def __init__(
        self,
        response: str,
        input_classification: ClassificationResult | None = None,
        output_check: OutputGuardrailResult | None = None,
        skipped_llm: bool = False,
        elapsed_seconds: float = 0.0,
    ):
        self.response = response
        self.input_classification = input_classification
        self.output_check = output_check
        self.skipped_llm = skipped_llm
        self.elapsed_seconds = elapsed_seconds


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def process_message(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
) -> PipelineResult:
    """Process a message through the full pipeline.

    Args:
        registry: Loaded AgentRegistry
        user_message: The user's text input
        message_history: Optional conversation history

    Returns:
        PipelineResult with response and metadata
    """
    start = time.monotonic()
    config = registry.config

    # ---------------------------------------------------------------
    # 1. Input guardrail (deterministic, pre-LLM)
    # ---------------------------------------------------------------
    classification = classify_input(user_message, config.guardrails)

    if classification.matched:
        action = classification.action

        # REDIRECT — respond immediately, skip LLM
        if action == GuardrailAction.REDIRECT:
            logger.info(
                "Pipeline: REDIRECT by '%s' → %s",
                classification.pattern_name,
                classification.redirect_message[:60],
            )
            return PipelineResult(
                response=classification.redirect_message,
                input_classification=classification,
                skipped_llm=True,
                elapsed_seconds=time.monotonic() - start,
            )

        # BLOCK — respond with safe message, skip LLM
        if action == GuardrailAction.BLOCK:
            safe = "Desculpe, não consigo ajudar com isso."
            logger.info("Pipeline: BLOCK by '%s'", classification.pattern_name)
            return PipelineResult(
                response=safe,
                input_classification=classification,
                skipped_llm=True,
                elapsed_seconds=time.monotonic() - start,
            )

        # BYPASS_LLM — for mechanical calculations (future: plug calc engine)
        if action == GuardrailAction.BYPASS_LLM:
            logger.info("Pipeline: BYPASS_LLM by '%s'", classification.pattern_name)
            # For now, fall through to LLM. When calc engine is built,
            # this will route to it instead.
            pass

        # INJECT — add extra instruction to the message for the LLM
        if action == GuardrailAction.INJECT and classification.inject_instruction:
            user_message = (
                f"{user_message}\n\n"
                f"[Instrução adicional: {classification.inject_instruction}]"
            )
            logger.info(
                "Pipeline: INJECT by '%s' → +%d chars",
                classification.pattern_name,
                len(classification.inject_instruction),
            )

        # ESCALATE / ESCALATE_NO_HABIT / TAKEOVER / TOOL_REQUIRED
        # These modify behavior but still go to LLM (with tool_choice forced, etc)
        # The actual escalation logic lives in human/ module (future)
        if action in (
            GuardrailAction.ESCALATE,
            GuardrailAction.ESCALATE_NO_HABIT,
            GuardrailAction.TAKEOVER,
            GuardrailAction.TOOL_REQUIRED,
        ):
            logger.info(
                "Pipeline: %s by '%s' (tool_choice=%s)",
                action.value, classification.pattern_name, classification.tool_choice,
            )
            # TODO: when human/ module is wired, handle escalation here
            # For now, pass through to LLM with the classification info

    # ---------------------------------------------------------------
    # 2. Run agent (LLM with automatic fallback)
    # ---------------------------------------------------------------
    response = await run_agent(
        registry,
        user_message,
        message_history,
    )

    # ---------------------------------------------------------------
    # 3. Output guardrail (post-LLM validation)
    # ---------------------------------------------------------------
    intent = classification.pattern_name if classification.matched else ""

    output_result = check_output(
        response=response,
        config=config.guardrails,
        intent=intent,
        tool_calls=None,  # TODO: extract from SDK result when wired
    )

    if output_result.blocked:
        logger.warning(
            "Pipeline: output blocked by '%s' → safe response",
            output_result.rule_name,
        )
        response = output_result.safe_response

    elapsed = time.monotonic() - start
    logger.info("Pipeline complete: %.1fs, blocked=%s", elapsed, output_result.blocked)

    return PipelineResult(
        response=response,
        input_classification=classification,
        output_check=output_result,
        skipped_llm=False,
        elapsed_seconds=elapsed,
    )
