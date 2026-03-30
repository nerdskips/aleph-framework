"""
Aleph Framework — Flow State
================================
Data model for in-progress flow state, stored as JSON in Redis.

Key: aleph:{client_id}:flow:{phone}

Fields:
  flow_id        — which flow is active
  step_id        — current step the user is on
  collected      — dict of {collect_as: user_reply} gathered so far
  started_at     — when the flow was first triggered (epoch seconds)
  step_started_at — when the current step was sent (epoch seconds)
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field


@dataclass
class FlowState:
    """In-progress flow state for a single phone number.

    Serialized to JSON for Redis storage. Mirrors the EscalationData
    pattern used in core/session/redis_escalation.py.
    """

    flow_id: str
    step_id: str
    collected: dict = field(default_factory=dict)
    started_at: float = field(default_factory=time.time)
    step_started_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {
            "flow_id": self.flow_id,
            "step_id": self.step_id,
            "collected": self.collected,
            "started_at": self.started_at,
            "step_started_at": self.step_started_at,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_dict(cls, data: dict) -> FlowState:
        return cls(
            flow_id=data["flow_id"],
            step_id=data["step_id"],
            collected=data.get("collected", {}),
            started_at=data.get("started_at", 0.0),
            step_started_at=data.get("step_started_at", 0.0),
        )

    @classmethod
    def from_json(cls, raw: str) -> FlowState:
        return cls.from_dict(json.loads(raw))
