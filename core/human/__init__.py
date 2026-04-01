"""Aleph Framework — Human-in-the-loop module.
Escalation + takeover + resolution.
"""

from __future__ import annotations

from core.human.escalation import (
    escalate_to_human,
    handle_human_response,
    build_notification_message,
    DEFAULT_HOLD_MESSAGE,
)

__all__ = [
    "escalate_to_human",
    "handle_human_response",
    "build_notification_message",
    "DEFAULT_HOLD_MESSAGE",
]