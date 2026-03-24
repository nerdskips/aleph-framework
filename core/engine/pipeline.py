"""
Zuper Agent Framework — Execution Pipeline
=============================================
The complete message processing flow:

  input text
    → input guardrail (deterministic, pre-LLM)
    → if redirect/block: return immediately (zero LLM cost)
    → if escalate + habits enabled: search habits first
      → if habit found: inject context, skip escalation, go to LLM
      → if no habit: escalate to human
    → run agent (LLM via Bifrost, with fallback)
    → output guardrail (post-LLM validation)
    → if blocked: return safe response
    → return agent response

This is what webhooks.py calls. It replaces direct run_agent calls.
"""

from __future__ import annotations

import logging
import time
from typing import Any

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
        escalated: bool = False,
        habit_used: bool = False,
        elapsed_seconds: float = 0.0,
    ):
        self.response = response
        self.input_classification = input_classification
        self.output_check = output_check
        self.skipped_llm = skipped_llm
        self.escalated = escalated
        self.habit_used = habit_used
        self.elapsed_seconds = elapsed_seconds


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

async def process_message(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
    phone: str = "",
    redis_session=None,
    sender=None,
    habits_db=None,
) -> PipelineResult:
    """Process a message through the full pipeline.

    Args:
        registry: Loaded AgentRegistry
        user_message: The user's text input
        message_history: Optional conversation history
        phone: Client phone number (needed for escalation)
        redis_session: RedisSession instance (needed for escalation)
        sender: ZAPISender instance (needed for escalation)
        habits_db: HabitsDatabase instance (needed for habit search)

    Returns:
        PipelineResult with response and metadata
    """
    start = time.monotonic()
    config = registry.config
    habit_used = False

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

        # -----------------------------------------------------------
        # ESCALATE — search habits first, then escalate if no match
        # -----------------------------------------------------------
        if action == GuardrailAction.ESCALATE:
            # Try habits BEFORE escalating (if enabled)
            habit_context = await _search_habits_if_enabled(
                config=config,
                habits_db=habits_db,
                user_message=user_message,
            )

            if habit_context:
                # Habit found! Inject context and go to LLM instead of escalating
                user_message = f"{user_message}\n\n{habit_context}"
                habit_used = True
                logger.info(
                    "Pipeline: ESCALATE by '%s' → habit found, skipping escalation",
                    classification.pattern_name,
                )
                # Fall through to LLM (step 2) with injected habit context
            else:
                # No habit found — escalate normally
                result = await _handle_escalation(
                    config=config,
                    phone=phone,
                    user_message=user_message,
                    redis_session=redis_session,
                    sender=sender,
                    classification=classification,
                    start=start,
                )
                if result:
                    return result

        # ESCALATE_NO_HABIT — always escalate, never check habits
        if action == GuardrailAction.ESCALATE_NO_HABIT:
            result = await _handle_escalation(
                config=config,
                phone=phone,
                user_message=user_message,
                redis_session=redis_session,
                sender=sender,
                classification=classification,
                start=start,
            )
            if result:
                return result

        # TAKEOVER — direct human takeover (different from escalation)
        if action == GuardrailAction.TAKEOVER:
            if redis_session and phone:
                await redis_session.activate_takeover(phone)
                logger.info("Pipeline: TAKEOVER activated for %s", phone)
                result = await _handle_escalation(
                    config=config,
                    phone=phone,
                    user_message=user_message,
                    redis_session=redis_session,
                    sender=sender,
                    classification=classification,
                    start=start,
                )
                if result:
                    return result

        # TOOL_REQUIRED — force tool_choice but continue to LLM
        if action == GuardrailAction.TOOL_REQUIRED:
            logger.info(
                "Pipeline: TOOL_REQUIRED by '%s' (tool_choice=%s)",
                classification.pattern_name, classification.tool_choice,
            )

    # ---------------------------------------------------------------
    # 1.5 Check if there's an active escalation for this phone
    # ---------------------------------------------------------------
    if redis_session and phone:
        if await redis_session.is_escalation_active(phone):
            logger.info(
                "Pipeline: escalation active for %s, holding message", phone,
            )
            return PipelineResult(
                response="Sua dúvida já está sendo verificada pela equipe. "
                         "Em breve retornaremos com a resposta!",
                skipped_llm=True,
                escalated=True,
                elapsed_seconds=time.monotonic() - start,
            )

    # ---------------------------------------------------------------
    # 2. Run agent (LLM with automatic fallback)
    # ---------------------------------------------------------------
    agent_result = await run_agent(
        registry,
        user_message,
        message_history,
    )

    response = agent_result.response
    tool_calls = agent_result.tool_calls

    # ---------------------------------------------------------------
    # 3. Output guardrail (post-LLM validation)
    # ---------------------------------------------------------------
    intent = classification.pattern_name if classification.matched else ""

    output_result = check_output(
        response=response,
        config=config.guardrails,
        intent=intent,
        tool_calls=tool_calls,
    )

    if output_result.blocked:
        logger.warning(
            "Pipeline: output blocked by '%s' → safe response",
            output_result.rule_name,
        )
        response = output_result.safe_response

    elapsed = time.monotonic() - start
    logger.info(
        "Pipeline complete: %.1fs, blocked=%s, tools=%s, habit=%s",
        elapsed, output_result.blocked, tool_calls or "(none)", habit_used,
    )

    return PipelineResult(
        response=response,
        input_classification=classification,
        output_check=output_result,
        skipped_llm=False,
        habit_used=habit_used,
        elapsed_seconds=elapsed,
    )


# ---------------------------------------------------------------------------
# Habits search (internal)
# ---------------------------------------------------------------------------

async def _search_habits_if_enabled(
    config,
    habits_db,
    user_message: str,
) -> str | None:
    """Search habits if enabled. Returns context string or None."""
    if not config.habits.enabled:
        return None

    if not config.habits.search_before_escalate:
        return None

    if not habits_db:
        logger.warning("Pipeline: habits enabled but no habits_db provided")
        return None

    try:
        from core.habits.search import search_and_format

        result = await search_and_format(
            db=habits_db,
            config=config.habits,
            client_id=config.client_id,
            query=user_message,
        )
        return result

    except Exception as e:
        logger.error("Pipeline: habit search failed: %s", str(e)[:200])
        return None


# ---------------------------------------------------------------------------
# Escalation handler (internal)
# ---------------------------------------------------------------------------

async def _handle_escalation(
    config,
    phone: str,
    user_message: str,
    redis_session,
    sender,
    classification: ClassificationResult,
    start: float,
) -> PipelineResult | None:
    """Handle escalation action. Returns PipelineResult if successful, None if failed."""
    if not config.human.enabled:
        logger.warning(
            "Pipeline: %s by '%s' but human.enabled=false, falling through to LLM",
            classification.action.value, classification.pattern_name,
        )
        return None

    if not redis_session or not sender or not phone:
        logger.error(
            "Pipeline: %s by '%s' but missing redis_session/sender/phone, "
            "falling through to LLM",
            classification.action.value, classification.pattern_name,
        )
        return None

    from core.human.escalation import escalate_to_human

    # Get client context from Redis (if available)
    context = await redis_session.get_context(phone)

    hold_message = await escalate_to_human(
        redis_session=redis_session,
        sender=sender,
        config=config,
        client_phone=phone,
        original_message=user_message,
        context=context,
    )

    if hold_message:
        logger.info(
            "Pipeline: %s by '%s' → escalated to human",
            classification.action.value, classification.pattern_name,
        )
        return PipelineResult(
            response=hold_message,
            input_classification=classification,
            skipped_llm=True,
            escalated=True,
            elapsed_seconds=time.monotonic() - start,
        )

    logger.warning(
        "Pipeline: escalation failed, falling through to LLM",
    )
    return None