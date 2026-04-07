"""
Aleph Framework — Execution Pipeline
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

from core.engine.runner import run_agent
from core.guardrails.input import ClassificationResult, classify_input
from core.guardrails.output import OutputGuardrailResult, check_output
from core.registry.registry import AgentRegistry
from core.registry.schema import GuardrailAction

logger = logging.getLogger("aleph.engine.pipeline")


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
        flow_active: bool = False,
        elapsed_seconds: float = 0.0,
        user_message: str = "",
        flow_collected: dict | None = None,
    ):
        self.response = response
        self.input_classification = input_classification
        self.output_check = output_check
        self.skipped_llm = skipped_llm
        self.escalated = escalated
        self.habit_used = habit_used
        self.flow_active = flow_active
        self.elapsed_seconds = elapsed_seconds
        self.user_message = user_message
        self.flow_collected = flow_collected or {}


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
    knowledge_db=None,
    flow_engine=None,
    episodic_memory=None,           # ← NEW
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
    _flow_step_reask: str = ""

    # Episodic memory — gap check + load context
    memory_ctx = None
    if episodic_memory is not None and phone:
        try:
            await episodic_memory.check_gap_compression(phone)
            memory_ctx = await episodic_memory.get_context(phone)
        except Exception as e:
            logger.warning("Episodic memory load failed (non-fatal): %s", e)

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
    # 1.6 Flow resolution (declarative state machine)
    # ---------------------------------------------------------------
    if config.flows.enabled and flow_engine and redis_session and phone:
        flow_result = await flow_engine.resolve(phone, user_message, redis_session)

        if flow_result.action in ("start", "advance", "hold"):
            # Send step question directly — skip LLM entirely
            logger.info("Pipeline: flow action=%s for %s", flow_result.action, phone)
            return PipelineResult(
                response=flow_result.message,
                skipped_llm=True,
                flow_active=True,
                elapsed_seconds=time.monotonic() - start,
            )

        if flow_result.action == "complete":
            result, user_message = await _handle_flow_complete(
                flow_result=flow_result,
                config=config,
                phone=phone,
                redis_session=redis_session,
                sender=sender,
                flow_engine=flow_engine,
                start=start,
                user_message=user_message,
            )
            if result is not None:
                return result
            # continue_to_llm: user_message now contains injected collected summary

        if flow_result.action == "pause":
            # Off-topic with pause: let LLM answer, append re-ask after response
            _flow_step_reask = flow_result.step_message
            logger.info("Pipeline: flow pause for %s, LLM will answer then re-ask step", phone)

        # action == "none": no flow active, continue normally

    # ---------------------------------------------------------------
    # 1.8 Knowledge search (pre-LLM, every message)
    # ---------------------------------------------------------------
    if config.knowledge.enabled and config.knowledge.auto_search and knowledge_db:
        try:
            from core.knowledge.search import search_and_format as knowledge_search

            knowledge_context = await knowledge_search(
                db=knowledge_db,
                config=config.knowledge,
                client_id=config.client_id,
                query=user_message,
            )

            if knowledge_context:
                user_message = f"{user_message}\n\n{knowledge_context}"
                logger.info("Pipeline: knowledge context injected (%d chars)", len(knowledge_context))

        except Exception as e:
            logger.error("Pipeline: knowledge search failed: %s", str(e)[:200])

    # ---------------------------------------------------------------
    # 2. Run agent (LLM with automatic fallback)
    # ---------------------------------------------------------------
    agent_result = await run_agent(
        registry,
        user_message,
        message_history,
        memory_ctx=memory_ctx,
    )

    # Save completed turn to episodic memory (fire-and-forget)
    if episodic_memory is not None and phone and agent_result.response:
        try:
            await episodic_memory.save_turn(phone, user_message, agent_result.response)
        except Exception as e:
            logger.warning("Episodic memory save failed (non-fatal): %s", e)

    response = agent_result.response
    tool_calls = agent_result.tool_calls

    # Append flow step re-ask if we were in pause mode (off-topic answered)
    if _flow_step_reask:
        response = f"{response}\n\n{_flow_step_reask}"

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

    result = PipelineResult(
        response=response,
        input_classification=classification,
        output_check=output_result,
        skipped_llm=False,
        habit_used=habit_used,
        elapsed_seconds=elapsed,
        user_message=user_message,
    )

    # D3 — background jobs (fire-and-forget, never raises)
    if config.queue.enabled:
        try:
            from core.queue.dispatcher import dispatch_jobs
            await dispatch_jobs(
                config=config,
                result=result,
                redis_session=redis_session,
                phone=phone,
                trigger="pipeline_complete",
            )
        except Exception as e:
            logger.error("Queue dispatch error: %s", e)

    return result


# ---------------------------------------------------------------------------
# Flow completion handler (internal)
# ---------------------------------------------------------------------------

async def _handle_flow_complete(
    flow_result,
    config,
    phone: str,
    redis_session,
    sender,
    flow_engine,
    start: float,
    user_message: str,
) -> tuple[PipelineResult | None, str]:
    """Handle on_complete action when a flow finishes.

    Returns (PipelineResult, user_message):
      - If PipelineResult is not None → return it immediately (skip LLM)
      - If None → fall through to LLM; user_message may be updated with collected summary
    """
    from core.registry.schema import OnCompleteAction

    oc = flow_result.on_complete
    if oc is None:
        return None, user_message

    action = oc.action
    elapsed = time.monotonic() - start

    # SEND_MESSAGE — static response, done
    if action == OnCompleteAction.SEND_MESSAGE:
        if not oc.message:
            logger.warning("Pipeline: flow complete with send_message but no message configured")
        return PipelineResult(
            response=oc.message,
            skipped_llm=True,
            flow_active=False,
            elapsed_seconds=elapsed,
        ), user_message

    # CONTINUE_TO_LLM — inject collected data and pass to LLM
    if action == OnCompleteAction.CONTINUE_TO_LLM:
        if oc.inject_summary and flow_result.collected:
            summary = _format_collected(flow_result.collected)
            user_message = f"{user_message}\n\n{summary}"
            logger.info("Pipeline: flow complete → continue_to_llm with collected summary (%d keys)",
                        len(flow_result.collected))
        return None, user_message

    # WEBHOOK — POST collected data to external URL
    if action == OnCompleteAction.WEBHOOK:
        if not oc.url:
            logger.error("Pipeline: flow complete with webhook but no url configured")
            return PipelineResult(
                response=oc.message or "Ocorreu um erro ao processar sua solicitação.",
                skipped_llm=True,
                flow_active=False,
                elapsed_seconds=elapsed,
            ), user_message

        webhook_response = await _call_flow_webhook(oc.url, oc.method, flow_result.collected)
        final_message = webhook_response
        if oc.then == "send_message" and oc.message:
            final_message = f"{webhook_response}\n\n{oc.message}" if webhook_response else oc.message
        elif not webhook_response:
            final_message = oc.message or ""

        logger.info("Pipeline: flow complete → webhook + then=%s", oc.then or "none")
        return PipelineResult(
            response=final_message,
            skipped_llm=True,
            flow_active=False,
            elapsed_seconds=elapsed,
        ), user_message

    # ESCALATE — hand off to human with collected data as context
    if action == OnCompleteAction.ESCALATE:
        summary = _format_collected(flow_result.collected)
        escalate_message = f"{user_message}\n\n{summary}" if flow_result.collected else user_message
        result = await _handle_escalation(
            config=config,
            phone=phone,
            user_message=escalate_message,
            redis_session=redis_session,
            sender=sender,
            classification=_NoMatchClassification(),
            start=start,
        )
        logger.info("Pipeline: flow complete → escalate")
        return result, user_message

    # START_FLOW — chain into another flow
    if action == OnCompleteAction.START_FLOW:
        if not oc.flow_id or not flow_engine:
            logger.warning("Pipeline: flow complete with start_flow but flow_id or engine missing")
            return None, user_message

        # Trigger the target flow as if the user had sent its first step
        from core.flows.state import FlowState
        target_flows = {f.id: f for f in config.flows.flows}
        target_flow = target_flows.get(oc.flow_id)
        if not target_flow or not target_flow.steps:
            logger.warning("Pipeline: start_flow target '%s' not found or has no steps", oc.flow_id)
            return None, user_message

        first_step = target_flow.steps[0]
        ttl = target_flow.state_ttl if target_flow.state_ttl > 0 else config.flows.default_state_ttl
        new_state = FlowState(flow_id=target_flow.id, step_id=first_step.id)
        await redis_session.set_flow_state(phone, new_state, ttl)

        logger.info("Pipeline: flow complete → start_flow '%s' step '%s'",
                    target_flow.id, first_step.id)
        return PipelineResult(
            response=first_step.message,
            skipped_llm=True,
            flow_active=True,
            elapsed_seconds=elapsed,
        ), user_message

    logger.warning("Pipeline: unknown on_complete action '%s', falling through to LLM", action)
    return None, user_message


def _format_collected(collected: dict) -> str:
    """Format collected flow data for LLM injection or escalation context."""
    lines = ["[DADOS COLETADOS NO FLUXO]"]
    for key, value in collected.items():
        lines.append(f"{key}: {value}")
    return "\n".join(lines)


async def _call_flow_webhook(url: str, method: str, collected: dict) -> str:
    """POST/GET collected flow data to a webhook URL.

    Returns the response text or empty string on failure.
    Mirrors the pattern used in core/tools/webhook.py.
    """
    try:
        import httpx
        async with httpx.AsyncClient(timeout=30.0) as client:
            if method.upper() == "GET":
                resp = await client.get(url, params=collected)
            else:
                resp = await client.post(url, json=collected)
            resp.raise_for_status()
            return resp.text
    except Exception as e:
        logger.error("Flow webhook call failed (%s %s): %s", method, url, str(e)[:200])
        return ""


class _NoMatchClassification:
    """Minimal ClassificationResult-like object for escalation calls without a guardrail match."""
    matched = False
    pattern_name = "flow_complete"
    action = None


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
