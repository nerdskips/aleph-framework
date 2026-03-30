"""
Aleph Framework — Flow Engine
===================================
Resolves the current flow state for a given phone + message.

Logic:
  1. No active flow → check triggers → start if matched, else "none"
  2. Active flow + off-topic (matches another flow's trigger) → hold or pause
  3. Active flow + normal answer → collect, advance or complete

Off-topic detection: a message that matches ANOTHER flow's trigger keywords/regex.
Text normalization mirrors core/guardrails/input.py (lowercase + remove accents).
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from dataclasses import dataclass, field

from core.registry.schema import FlowDefinition, FlowsConfig, OnCompleteConfig, OnInterruptAction, StepConfig

logger = logging.getLogger("aleph.flows")


# ---------------------------------------------------------------------------
# Resolution result
# ---------------------------------------------------------------------------

@dataclass
class FlowResolution:
    """Result returned by FlowEngine.resolve().

    action values:
      "none"     — no flow involved, pipeline continues normally
      "start"    — flow triggered, first step sent (message = step question)
      "advance"  — step answered, next step sent (message = next step question)
      "complete" — last step answered, on_complete ready to execute
      "hold"     — off-topic during flow with on_interrupt=hold; re-ask step (message = step question)
      "pause"    — off-topic during flow with on_interrupt=pause; let LLM answer,
                   then append step_message to the LLM response
    """
    action: str
    message: str = ""
    collected: dict = field(default_factory=dict)
    on_complete: OnCompleteConfig | None = None
    step_message: str = ""    # used by "pause": appended after LLM response


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


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------

class FlowEngine:
    """Stateless engine — all state lives in Redis via redis_session."""

    def __init__(self, config: FlowsConfig):
        self.config = config
        # Build lookup maps for O(1) access
        self._flows: dict[str, FlowDefinition] = {f.id: f for f in config.flows}
        self._steps: dict[str, dict[str, StepConfig]] = {
            f.id: {s.id: s for s in f.steps}
            for f in config.flows
        }

    def _resolve_ttl(self, flow: FlowDefinition) -> int:
        """Return effective TTL: flow-level override or global default."""
        return flow.state_ttl if flow.state_ttl > 0 else self.config.default_state_ttl

    def _find_trigger_match(self, message: str) -> FlowDefinition | None:
        """Return the first flow whose trigger matches the message, or None."""
        for flow in self.config.flows:
            if _matches_trigger(message, flow):
                return flow
        return None

    async def resolve(
        self,
        phone: str,
        message: str,
        redis_session,
    ) -> FlowResolution:
        """Resolve the flow action for a given phone + message.

        Args:
            phone: The user's phone number (Redis key scope)
            message: The user's text message
            redis_session: RedisSession instance for state persistence

        Returns:
            FlowResolution describing what the pipeline should do.
        """
        from core.flows.state import FlowState

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

            first_step = matched_flow.steps[0]
            new_state = FlowState(
                flow_id=matched_flow.id,
                step_id=first_step.id,
            )
            ttl = self._resolve_ttl(matched_flow)
            await redis_session.set_flow_state(phone, new_state, ttl)

            logger.info("Flow '%s' started for %s → step '%s'",
                        matched_flow.id, phone, first_step.id)
            return FlowResolution(action="start", message=first_step.message)

        # -------------------------------------------------------------------
        # Case 2: Active flow
        # -------------------------------------------------------------------
        flow = self._flows.get(state.flow_id)
        if flow is None:
            # Flow definition removed from config — clear stale state
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

        # Off-topic detection: matches a DIFFERENT flow's trigger
        for other_flow in self.config.flows:
            if other_flow.id != state.flow_id and _matches_trigger(message, other_flow):
                logger.info(
                    "Off-topic detected for %s during flow '%s' "
                    "(matches flow '%s') → on_interrupt=%s",
                    phone, state.flow_id, other_flow.id, flow.on_interrupt.value,
                )
                if flow.on_interrupt == OnInterruptAction.HOLD:
                    return FlowResolution(action="hold", message=current_step.message)
                else:  # PAUSE
                    return FlowResolution(action="pause", step_message=current_step.message)

        # Normal answer: collect and advance
        if current_step.collect_as:
            state.collected[current_step.collect_as] = message
            logger.debug("Flow '%s' collected %s='%s' for %s",
                         flow.id, current_step.collect_as, message[:60], phone)

        if current_step.next:
            next_step = steps.get(current_step.next)
            if next_step is None:
                logger.error("Next step '%s' not found in flow '%s', completing flow",
                             current_step.next, flow.id)
                await redis_session.clear_flow_state(phone)
                return FlowResolution(
                    action="complete",
                    collected=state.collected,
                    on_complete=current_step.on_complete,
                )

            state.step_id = next_step.id
            state.step_started_at = time.time()
            ttl = self._resolve_ttl(flow)
            await redis_session.set_flow_state(phone, state, ttl)

            logger.info("Flow '%s' advanced for %s → step '%s'",
                        flow.id, phone, next_step.id)
            return FlowResolution(action="advance", message=next_step.message)

        # Last step — flow complete
        await redis_session.clear_flow_state(phone)
        logger.info("Flow '%s' completed for %s (collected: %s)",
                    flow.id, phone, list(state.collected.keys()))
        return FlowResolution(
            action="complete",
            collected=state.collected,
            on_complete=current_step.on_complete,
        )
