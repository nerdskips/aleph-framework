"""
Aleph Framework — Flow Engine v2
===================================
Resolves the current flow state for a given phone + message.

Logic:
  1. No active flow → check triggers → start if matched, else "none"
  2. Active flow + cancel_if match → cancel flow
  3. Active flow + step timeout exceeded → re_ask / cancel / escalate
  4. Active flow + off-topic (matches another flow's trigger) → hold or pause
  5. Active flow + message step → validate (tool-based) → collect → advance
  6. Auto-advance through lookup / branch steps until a message step or complete

New FlowResolution action values (Phase 15):
  "cancelled"           — cancel_if matched; message = cancel_message
  "validate_fail"       — validation failed; message = re-ask; validation_injection for LLM
  "validate_exceeded"   — max retries hit; on_exceed action fires
  "timeout_reask"       — step timeout, action=re_ask; message = re-ask text
  "timeout_cancelled"   — step timeout, action=cancel
  "timeout_escalated"   — step timeout, action=escalate

Text normalization mirrors core/guardrails/input.py (lowercase + remove accents).
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

from core.registry.schema import (
    FlowDefinition,
    FlowsConfig,
    OnCompleteConfig,
    OnInterruptAction,
    StepConfig,
    StepType,
    ToolRef,
)

logger = logging.getLogger("aleph.flows")

# Max non-message steps to execute in one resolve() call (safety limit)
_MAX_AUTO_STEPS = 30


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass
class FlowResolution:
    """Result returned by FlowEngine.resolve().

    action values:
      "none"              — no flow involved, pipeline continues normally
      "start"             — flow triggered, first step sent
      "advance"           — step answered, next step sent
      "complete"          — last step answered, on_complete ready to execute
      "hold"              — off-topic during flow with on_interrupt=hold; re-ask step
      "pause"             — off-topic; LLM answers, then append step_message
      "cancelled"         — cancel_if matched; message = cancel_message (Phase 15)
      "validate_fail"     — validation failed; stay on step; inject validation_injection (Phase 15)
      "validate_exceeded" — max retries hit; on_exceed configured in validate field (Phase 15)
      "timeout_reask"     — step timed out, action=re_ask (Phase 15)
      "timeout_cancelled" — step timed out, action=cancel (Phase 15)
      "timeout_escalated" — step timed out, action=escalate (Phase 15)
    """
    action: str
    message: str = ""
    collected: dict = field(default_factory=dict)
    on_complete: OnCompleteConfig | None = None
    step_message: str = ""             # used by "pause": appended after LLM response
    validation_injection: str = ""     # used by "validate_fail": injected into LLM context
    on_exceed: str = ""                # used by "validate_exceeded": escalate | cancel
    exceed_message: str = ""           # used by "validate_exceeded": cancel message


# ---------------------------------------------------------------------------
# Text normalization (mirrors core/guardrails/input.py)
# ---------------------------------------------------------------------------

def _normalize(text: str) -> str:
    text = text.lower()
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c)).strip()


def _matches_trigger(text: str, flow: FlowDefinition) -> bool:
    """Check if normalized text matches this flow's trigger keywords or regex."""
    normalized = _normalize(text)
    for kw in flow.trigger_keywords:
        if _normalize(kw) in normalized:
            return True
    for pattern in flow.trigger_regex:
        try:
            if re.search(pattern, normalized):
                return True
        except re.error:
            logger.warning("Invalid trigger regex in flow '%s': %s", flow.id, pattern)
    return False


def _matches_cancel_if(text: str, flow: FlowDefinition) -> bool:
    """Check if message matches any of the flow's cancel_if patterns."""
    normalized = _normalize(text)
    for pattern in flow.cancel_if:
        try:
            if _normalize(pattern) in normalized or re.search(pattern, normalized):
                return True
        except re.error:
            if _normalize(pattern) in normalized:
                return True
    return False


# ---------------------------------------------------------------------------
# Collected data — sensitive field redaction
# ---------------------------------------------------------------------------

def _public_collected(state, flow: FlowDefinition) -> dict:
    """Return state.collected with sensitive fields replaced by '[REDACTED]'."""
    sensitive_keys = {s.collect_as for s in flow.steps if s.sensitive and s.collect_as}
    if not sensitive_keys:
        return dict(state.collected)
    return {
        k: "[REDACTED]" if k in sensitive_keys else v
        for k, v in state.collected.items()
    }


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class FlowEngine:
    """Stateless flow engine — all state lives in Redis via redis_session.

    Args:
        config: FlowsConfig (flows section of FrameworkConfig).
        tools:  List of ToolRef from the agent's tools config, used for
                validation tool lookup. Pass config.tools when instantiating.
    """

    def __init__(self, config: FlowsConfig, tools: list[ToolRef] | None = None):
        self.config = config
        self._tools_by_name: dict[str, ToolRef] = {t.name: t for t in (tools or [])}
        # Build lookup maps for O(1) access
        self._flows: dict[str, FlowDefinition] = {f.id: f for f in config.flows}
        self._steps: dict[str, dict[str, StepConfig]] = {
            f.id: {s.id: s for s in f.steps}
            for f in config.flows
        }

    def _resolve_ttl(self, flow: FlowDefinition) -> int:
        return flow.state_ttl if flow.state_ttl > 0 else self.config.default_state_ttl

    def _find_trigger_match(self, message: str) -> FlowDefinition | None:
        for flow in self.config.flows:
            if _matches_trigger(message, flow):
                return flow
        return None

    # ------------------------------------------------------------------
    # Public entry point
    # ------------------------------------------------------------------

    async def resolve(
        self,
        phone: str,
        message: str,
        redis_session,
    ) -> FlowResolution:
        """Resolve the flow action for a given phone + message."""
        from core.flows.state import FlowState
        from core.flows.template import render

        state = await redis_session.get_flow_state(phone)

        # -------------------------------------------------------------------
        # Case 1: No active flow
        # -------------------------------------------------------------------
        if state is None:
            matched_flow = self._find_trigger_match(message)
            if matched_flow is None:
                return FlowResolution(action="none")

            if not matched_flow.steps:
                logger.warning("Flow '%s' has no steps, ignoring trigger", matched_flow.id)
                return FlowResolution(action="none")

            new_state = FlowState(flow_id=matched_flow.id, step_id=matched_flow.steps[0].id)
            logger.info("Flow '%s' started for %s", matched_flow.id, phone)

            # Advance through non-message steps to the first message step
            return await self._advance_to_message(new_state, matched_flow, redis_session, phone, "start")

        # -------------------------------------------------------------------
        # Case 2: Active flow
        # -------------------------------------------------------------------
        flow = self._flows.get(state.flow_id)
        if flow is None:
            logger.warning("Active flow '%s' not found in config, clearing state for %s",
                           state.flow_id, phone)
            await redis_session.clear_flow_state(phone)
            return FlowResolution(action="none")

        steps = self._steps.get(state.flow_id, {})
        current_step = steps.get(state.step_id)
        if current_step is None:
            logger.warning("Step '%s' not found in flow '%s', clearing state for %s",
                           state.step_id, state.flow_id, phone)
            await redis_session.clear_flow_state(phone)
            return FlowResolution(action="none")

        # --- cancel_if check ---
        if flow.cancel_if and _matches_cancel_if(message, flow):
            logger.info("Flow '%s' cancelled for %s (cancel_if matched)", flow.id, phone)
            await redis_session.clear_flow_state(phone)
            cancel_msg = render(flow.cancel_message, state.collected)
            return FlowResolution(action="cancelled", message=cancel_msg)

        # --- step timeout check ---
        timeout_mins = current_step.step_timeout_minutes or flow.default_step_timeout_minutes
        if timeout_mins > 0:
            elapsed_secs = time.time() - state.step_started_at
            if elapsed_secs > timeout_mins * 60:
                return await self._handle_timeout(flow, state, current_step, redis_session, phone)

        # --- off-topic detection ---
        for other_flow in self.config.flows:
            if other_flow.id != state.flow_id and _matches_trigger(message, other_flow):
                logger.info(
                    "Off-topic detected for %s during flow '%s' "
                    "(matches flow '%s') → on_interrupt=%s",
                    phone, state.flow_id, other_flow.id, flow.on_interrupt.value,
                )
                if flow.on_interrupt == OnInterruptAction.HOLD:
                    return FlowResolution(action="hold", message=render(current_step.message, state.collected))
                else:  # PAUSE
                    return FlowResolution(action="pause", step_message=render(current_step.message, state.collected))

        # If the active step is a non-message type (lookup/branch), execute it now.
        # This can happen if a flow starts with a lookup/branch, or if state was
        # persisted mid-auto-advance (edge case). Auto-advance handles it.
        if current_step.type != StepType.MESSAGE:
            return await self._advance_to_message(state, flow, redis_session, phone, "advance")

        # --- validation ---
        if (
            current_step.type == StepType.MESSAGE
            and current_step.validation
            and current_step.validation.tool
        ):
            valid, inject_msg = await self._call_validation_tool(
                current_step, message, state
            )
            if not valid:
                retries = state.retry_counts.get(current_step.id, 0)
                if retries >= current_step.validation.max_retries:
                    logger.info(
                        "Flow '%s' validation exceeded for step '%s' for %s",
                        flow.id, current_step.id, phone,
                    )
                    await redis_session.clear_flow_state(phone)
                    return FlowResolution(
                        action="validate_exceeded",
                        on_exceed=current_step.validation.on_exceed,
                        exceed_message=current_step.validation.exceed_message or flow.cancel_message,
                        collected=_public_collected(state, flow),
                    )
                state.retry_counts[current_step.id] = retries + 1
                ttl = self._resolve_ttl(flow)
                await redis_session.set_flow_state(phone, state, ttl)
                logger.info(
                    "Flow '%s' validation fail for step '%s' for %s (retry %d/%d)",
                    flow.id, current_step.id, phone, retries + 1, current_step.validation.max_retries,
                )
                return FlowResolution(
                    action="validate_fail",
                    message=render(current_step.message, state.collected),
                    validation_injection=inject_msg,
                )

        # --- collect answer ---
        if current_step.collect_as:
            log_val = "[REDACTED]" if current_step.sensitive else message[:60]
            state.collected[current_step.collect_as] = message
            logger.debug("Flow '%s' collected %s='%s' for %s",
                         flow.id, current_step.collect_as, log_val, phone)

        # --- advance ---
        if current_step.next:
            state.step_id = current_step.next
            state.step_started_at = time.time()
            return await self._advance_to_message(state, flow, redis_session, phone, "advance")

        # Last step — complete
        await redis_session.clear_flow_state(phone)
        logger.info("Flow '%s' completed for %s (collected keys: %s)",
                    flow.id, phone, list(state.collected.keys()))
        return FlowResolution(
            action="complete",
            collected=_public_collected(state, flow),
            on_complete=current_step.on_complete,
        )

    # ------------------------------------------------------------------
    # Auto-advance loop (non-message steps)
    # ------------------------------------------------------------------

    async def _advance_to_message(
        self,
        state,
        flow: FlowDefinition,
        redis_session,
        phone: str,
        action_label: str,
    ) -> FlowResolution:
        """Step through lookup/branch/skip_if steps until a message step or complete."""
        from core.flows.expr import evaluate
        from core.flows.template import render

        ttl = self._resolve_ttl(flow)
        steps = self._steps.get(flow.id, {})

        for _ in range(_MAX_AUTO_STEPS):
            step = steps.get(state.step_id)
            if step is None:
                logger.error("Step '%s' missing in flow '%s' for %s — completing",
                             state.step_id, flow.id, phone)
                await redis_session.clear_flow_state(phone)
                return FlowResolution(action="complete", collected=_public_collected(state, flow))

            # skip_if
            if step.skip_if and evaluate(step.skip_if, state.collected):
                logger.debug("Flow '%s' skipping step '%s' for %s (skip_if)", flow.id, step.id, phone)
                if step.next:
                    state.step_id = step.next
                    state.step_started_at = time.time()
                    continue
                await redis_session.clear_flow_state(phone)
                return FlowResolution(
                    action="complete",
                    collected=_public_collected(state, flow),
                    on_complete=step.on_complete,
                )

            if step.type == StepType.LOOKUP:
                next_id, err = await self._exec_lookup(step, state, flow, redis_session, phone)
                if err is not None:
                    return err
                next_step_id = next_id or step.next
                if not next_step_id:
                    await redis_session.clear_flow_state(phone)
                    return FlowResolution(
                        action="complete",
                        collected=_public_collected(state, flow),
                        on_complete=step.on_complete,
                    )
                state.step_id = next_step_id
                state.step_started_at = time.time()
                continue

            if step.type == StepType.BRANCH:
                next_id = self._eval_branch(step, state)
                if not next_id:
                    logger.warning("Branch '%s' in flow '%s' had no match — completing", step.id, flow.id)
                    await redis_session.clear_flow_state(phone)
                    return FlowResolution(action="complete", collected=_public_collected(state, flow))
                state.step_id = next_id
                state.step_started_at = time.time()
                continue

            # Message step — save and return
            await redis_session.set_flow_state(phone, state, ttl)
            logger.info("Flow '%s' %s for %s → step '%s'",
                        flow.id, action_label, phone, step.id)
            return FlowResolution(action=action_label, message=render(step.message, state.collected))

        # Safety — should never happen in practice
        logger.error("Flow '%s': exceeded %d auto-step limit for %s", flow.id, _MAX_AUTO_STEPS, phone)
        await redis_session.clear_flow_state(phone)
        return FlowResolution(action="complete", collected=_public_collected(state, flow))

    # ------------------------------------------------------------------
    # Lookup step execution
    # ------------------------------------------------------------------

    async def _exec_lookup(
        self,
        step: StepConfig,
        state,
        flow: FlowDefinition,
        redis_session,
        phone: str,
    ) -> tuple[str | None, FlowResolution | None]:
        """Execute a lookup step. Returns (next_step_id, None) on success or (None, error_resolution)."""
        from core.flows.template import render, render_dict

        if not step.lookup:
            logger.error("Lookup step '%s' has no lookup config in flow '%s'", step.id, flow.id)
            await redis_session.clear_flow_state(phone)
            return None, FlowResolution(action="complete", collected=_public_collected(state, flow))

        cfg = step.lookup
        url = render(cfg.url, state.collected)
        payload = render_dict(cfg.payload, state.collected)
        headers = dict(cfg.headers)

        response_data = await _call_webhook_with_retry(
            url=url,
            method=cfg.method,
            payload=payload,
            headers=headers,
            timeout_seconds=cfg.timeout_seconds,
            retry_attempts=cfg.retry_attempts,
            retry_backoff_seconds=cfg.retry_backoff_seconds,
        )

        if response_data is None:
            # HTTP error after all retries
            logger.warning("Lookup step '%s' failed for %s — on_error=%s", step.id, phone, cfg.on_error)
            return await self._handle_lookup_error(cfg, state, flow, redis_session, phone)

        # Extract response_key if specified
        if cfg.response_key and isinstance(response_data, dict):
            for part in cfg.response_key.split("."):
                if isinstance(response_data, dict):
                    response_data = response_data.get(part)
                else:
                    response_data = None
                    break

        if step.collect_as:
            log_val = "[REDACTED]" if step.sensitive else str(response_data)[:80]
            state.collected[step.collect_as] = response_data
            logger.debug("Flow '%s' lookup stored %s='%s' for %s",
                         flow.id, step.collect_as, log_val, phone)

        return None, None  # let caller advance to step.next

    async def _handle_lookup_error(
        self,
        cfg,
        state,
        flow: FlowDefinition,
        redis_session,
        phone: str,
    ) -> tuple[str | None, FlowResolution | None]:
        """Route lookup errors according to on_error config."""
        from core.flows.template import render

        on_error = cfg.on_error

        if on_error == "jump_to" and cfg.error_jump:
            return cfg.error_jump, None

        if on_error == "continue":
            return None, None  # advance to step.next normally

        if on_error == "cancel":
            await redis_session.clear_flow_state(phone)
            return None, FlowResolution(action="cancelled", message=render(flow.cancel_message, state.collected))

        # Default: escalate
        await redis_session.clear_flow_state(phone)
        return None, FlowResolution(action="complete", collected=_public_collected(state, flow))

    # ------------------------------------------------------------------
    # Branch step evaluation
    # ------------------------------------------------------------------

    def _eval_branch(self, step: StepConfig, state) -> str:
        """Evaluate branch conditions in order. Return the jump_to step ID or ''."""
        from core.flows.expr import evaluate

        for condition in step.conditions:
            if condition.if_expr:
                if evaluate(condition.if_expr, state.collected):
                    logger.debug("Branch '%s' condition matched: %s → %s",
                                 step.id, condition.if_expr, condition.jump_to)
                    return condition.jump_to
            elif condition.else_jump:
                logger.debug("Branch '%s' else clause → %s", step.id, condition.else_jump)
                return condition.else_jump

        return ""

    # ------------------------------------------------------------------
    # Validation tool call
    # ------------------------------------------------------------------

    async def _call_validation_tool(
        self,
        step: StepConfig,
        message: str,
        state,
    ) -> tuple[bool, str]:
        """Call the configured validation tool webhook.

        Returns (valid, inject_message).
        The inject_message is passed to the LLM when validation fails
        so the agent responds naturally instead of a canned error.
        """
        tool_name = step.validation.tool  # type: ignore[union-attr]
        tool_ref = self._tools_by_name.get(tool_name)

        if not tool_ref:
            logger.warning("Validation tool '%s' not found in tools config — skipping validation", tool_name)
            return True, ""

        if not tool_ref.webhook_url:
            logger.warning("Validation tool '%s' has no webhook_url — skipping validation", tool_name)
            return True, ""

        payload = {
            "message": message,
            "collected": dict(state.collected),
        }

        result = await _call_webhook_with_retry(
            url=tool_ref.webhook_url,
            method=tool_ref.method or "POST",
            payload=payload,
            headers={},
            timeout_seconds=tool_ref.timeout_seconds,
            retry_attempts=1,
            retry_backoff_seconds=0.0,
        )

        if result is None:
            logger.warning("Validation tool '%s' returned error — treating as valid (fail-open)", tool_name)
            return True, ""

        if not isinstance(result, dict):
            logger.warning("Validation tool '%s' returned non-dict: %s — treating as valid", tool_name, type(result))
            return True, ""

        valid = bool(result.get("valid", True))
        msg = str(result.get("message", ""))
        return valid, msg

    # ------------------------------------------------------------------
    # Step timeout handler
    # ------------------------------------------------------------------

    async def _handle_timeout(
        self,
        flow: FlowDefinition,
        state,
        current_step: StepConfig,
        redis_session,
        phone: str,
    ) -> FlowResolution:
        """Handle step timeout based on flow's default_timeout_action."""
        from core.flows.template import render

        action = flow.default_timeout_action
        logger.info("Flow '%s' step '%s' timed out for %s → action=%s",
                    flow.id, current_step.id, phone, action)

        if action == "re_ask":
            msg = flow.default_timeout_message or current_step.message
            return FlowResolution(action="timeout_reask", message=render(msg, state.collected))

        if action == "escalate":
            await redis_session.clear_flow_state(phone)
            return FlowResolution(action="timeout_escalated", collected=_public_collected(state, flow))

        # cancel
        await redis_session.clear_flow_state(phone)
        return FlowResolution(
            action="timeout_cancelled",
            message=render(flow.cancel_message, state.collected),
        )


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------

async def _call_webhook_with_retry(
    url: str,
    method: str,
    payload: dict,
    headers: dict,
    timeout_seconds: int,
    retry_attempts: int,
    retry_backoff_seconds: float,
) -> dict | None:
    """POST/GET to a webhook URL with exponential backoff retries.

    Returns parsed JSON dict on success, or None on all failures.
    """
    try:
        import httpx
    except ImportError:
        logger.error("httpx not installed — cannot call lookup/validation webhooks")
        return None

    attempt = 0
    last_exc: Exception | None = None

    while attempt <= retry_attempts:
        if attempt > 0:
            backoff = retry_backoff_seconds * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)
            logger.debug("Webhook retry %d/%d for %s (backoff=%.1fs)", attempt, retry_attempts, url, backoff)

        try:
            async with httpx.AsyncClient(timeout=timeout_seconds) as client:
                if method.upper() == "GET":
                    resp = await client.get(url, params=payload, headers=headers)
                else:
                    resp = await client.post(url, json=payload, headers=headers)

                if resp.status_code < 500:
                    if resp.status_code >= 400:
                        logger.warning("Webhook returned %d for %s", resp.status_code, url)
                        return None
                    try:
                        return resp.json()
                    except Exception:
                        return {"_raw": resp.text}

                # 5xx — retry
                last_exc = RuntimeError(f"HTTP {resp.status_code}")

        except Exception as exc:
            last_exc = exc

        attempt += 1

    logger.error("Webhook %s failed after %d attempts: %s", url, retry_attempts + 1, last_exc)
    return None
