"""
Aleph Framework — Self-Awareness Injector
==========================================
Applies relevance gates and formats AwarenessState into a system prompt block.
Pure functions — no I/O, no Redis, fully testable.
"""

from __future__ import annotations

import logging

from core.awareness.reader import AwarenessState
from core.registry.schema import SelfAwarenessConfig

logger = logging.getLogger("aleph.awareness.injector")


def should_inject(cfg: SelfAwarenessConfig, state: AwarenessState) -> bool:
    """Relevance gates — all must pass for injection to fire.

    Gates:
      1. User must have interacted before (elapsed > 0)
      2. Gap must exceed return_gap_minutes (returning user, not mid-conversation)
      3. State must be younger than max_injection_age_hours
      4. At least one piece of content must exist (summary, flow, or escalation)
    """
    if state.elapsed_minutes <= 0:
        return False  # new user, nothing to inject

    if state.elapsed_minutes < cfg.return_gap_minutes:
        return False  # still in active conversation, no injection needed

    if state.elapsed_minutes > cfg.max_injection_age_hours * 60:
        return False  # state too old to be reliable

    has_content = (
        (cfg.include_summary and bool(state.summary))
        or (cfg.include_flow and state.flow_id is not None)
        or (cfg.include_escalation and state.escalation_active)
    )
    return has_content


def build_injection(cfg: SelfAwarenessConfig, state: AwarenessState) -> str:
    """Format AwarenessState into a concise system prompt block.

    The output is appended to the agent's system instructions.
    It is NOT added to conversation history — invisible to the user.
    """
    lines = ["[Contexto do retorno do usuário]"]

    if cfg.include_summary and state.summary:
        lines.append(f"Resumo das conversas anteriores:\n{state.summary}")

    if cfg.include_flow and state.flow_id:
        step_info = f", passo atual: {state.flow_step}" if state.flow_step else ""
        lines.append(f"Fluxo interrompido: {state.flow_id}{step_info}")
        lines.append("Sugestão: pergunte educadamente se deseja retomar ou começar do zero.")

    if cfg.include_escalation and state.escalation_active:
        lines.append("Escalação humana estava ativa quando o usuário saiu.")
        lines.append("Verifique o status com o atendente antes de responder.")

    elapsed_h = state.elapsed_minutes / 60
    lines.append(f"(Usuário retornou após {elapsed_h:.1f}h de inatividade)")

    return "\n".join(lines)
