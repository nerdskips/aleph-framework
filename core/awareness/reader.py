"""
Aleph Framework — Self-Awareness State Reader
==============================================
Reads and interprets Redis state for self-awareness context injection.
Returns a typed AwarenessState — never raw Redis data.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.awareness.reader")


@dataclass
class AwarenessState:
    """Interpreted agent state — clean structured output for the injector."""
    summary: str                  # episodic memory summary (may be empty)
    flow_id: str | None           # active/interrupted flow ID
    flow_step: str | None         # current step within the flow
    escalation_active: bool       # human escalation currently active
    elapsed_minutes: float        # minutes since last interaction


async def build_awareness_state(
    config: FrameworkConfig,
    memory_ctx,                   # MemoryContext from EpisodicMemory
    flow_state,                   # FlowState | None from redis_session
    escalation,                   # EscalationData | None from redis_session
) -> AwarenessState:
    """Build an interpreted state snapshot from available context sources."""
    # Elapsed time since last turn
    elapsed_minutes = 0.0
    if memory_ctx and memory_ctx.last_turn_ts > 0:
        elapsed_minutes = (time.time() - memory_ctx.last_turn_ts) / 60

    # Episodic summary
    summary = (memory_ctx.summary or "") if memory_ctx else ""

    # Flow state
    flow_id = None
    flow_step = None
    if flow_state is not None:
        flow_id = getattr(flow_state, "flow_id", None)
        flow_step = getattr(flow_state, "step_id", None)

    # Escalation
    escalation_active = escalation is not None

    return AwarenessState(
        summary=summary,
        flow_id=flow_id,
        flow_step=flow_step,
        escalation_active=escalation_active,
        elapsed_minutes=elapsed_minutes,
    )
