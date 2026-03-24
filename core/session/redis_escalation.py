"""
Zuper Agent Framework — Redis Escalation Operations
======================================================
Extension methods for RedisSession to handle escalation state.

These methods manage the escalation lifecycle:
  - Save escalation (pause agent, store context)
  - Load escalation (retrieve context when human responds)
  - Clear escalation (cleanup after resolution)
  - Map notification messageId → client phone (for quote tracking)

Keys:
  zuper:{client_id}:esc:{phone}         → Escalation session JSON
  zuper:{client_id}:esc_msg:{messageId} → Client phone (reverse lookup from quote)
"""
# NOTE: These methods should be added to RedisSession in core/session/redis.py
# They are here as a separate file for development reference and clean diff.
# After review, merge into redis.py.

from __future__ import annotations

import json
import logging
import time
from typing import Any

logger = logging.getLogger("zuper.session")


# ---------------------------------------------------------------------------
# Data structure for escalation session
# ---------------------------------------------------------------------------

class EscalationData:
    """Escalation session data stored in Redis."""

    def __init__(
        self,
        client_phone: str,
        original_message: str,
        context: dict[str, Any] | None = None,
        responsible_phone: str = "",
        notification_message_id: str = "",
        agent_name: str = "",
        timestamp: float | None = None,
    ):
        self.client_phone = client_phone
        self.original_message = original_message
        self.context = context or {}
        self.responsible_phone = responsible_phone
        self.notification_message_id = notification_message_id
        self.agent_name = agent_name
        self.timestamp = timestamp or time.time()

    def to_dict(self) -> dict:
        return {
            "client_phone": self.client_phone,
            "original_message": self.original_message,
            "context": self.context,
            "responsible_phone": self.responsible_phone,
            "notification_message_id": self.notification_message_id,
            "agent_name": self.agent_name,
            "timestamp": self.timestamp,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EscalationData":
        return cls(
            client_phone=data["client_phone"],
            original_message=data["original_message"],
            context=data.get("context", {}),
            responsible_phone=data.get("responsible_phone", ""),
            notification_message_id=data.get("notification_message_id", ""),
            agent_name=data.get("agent_name", ""),
            timestamp=data.get("timestamp", 0),
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "EscalationData":
        return cls.from_dict(json.loads(raw))


# ---------------------------------------------------------------------------
# Methods to add to RedisSession
# ---------------------------------------------------------------------------
# Copy these methods into the RedisSession class in core/session/redis.py

"""
    # -------------------------------------------------------------------
    # Escalation — pause + resume flow
    # -------------------------------------------------------------------

    async def save_escalation(self, data: "EscalationData") -> None:
        \"\"\"Save escalation session for a client phone.
        Called when the pipeline escalates — agent pauses, human takes over.\"\"\"
        from core.session.redis_escalation import EscalationData  # noqa: F811

        key = self._key("esc", data.client_phone)
        ttl = self.config.human.escalation_session_ttl
        await self.client.set(key, data.to_json(), ex=ttl)
        logger.info(
            "Escalation saved for %s → responsible %s (TTL: %ds)",
            data.client_phone, data.responsible_phone, ttl,
        )

    async def get_escalation(self, client_phone: str) -> "EscalationData | None":
        \"\"\"Load escalation session for a client phone.
        Returns None if no active escalation or TTL expired.\"\"\"
        from core.session.redis_escalation import EscalationData  # noqa: F811

        key = self._key("esc", client_phone)
        raw = await self.client.get(key)
        if not raw:
            return None
        try:
            return EscalationData.from_json(raw)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse escalation for %s: %s", client_phone, e)
            return None

    async def clear_escalation(self, client_phone: str) -> None:
        \"\"\"Clear escalation session after human resolves it.\"\"\"
        key = self._key("esc", client_phone)
        await self.client.delete(key)
        logger.info("Escalation cleared for %s", client_phone)

    async def is_escalation_active(self, client_phone: str) -> bool:
        \"\"\"Check if there's an active escalation for this phone.\"\"\"
        key = self._key("esc", client_phone)
        return bool(await self.client.exists(key))

    async def map_notification_to_client(
        self, notification_message_id: str, client_phone: str
    ) -> None:
        \"\"\"Map a notification messageId to the client phone.
        Used for reverse lookup when human responds with quote.\"\"\"
        key = self._key("esc_msg", notification_message_id)
        ttl = self.config.human.escalation_session_ttl
        await self.client.set(key, client_phone, ex=ttl)
        logger.debug(
            "Notification mapped: %s → %s", notification_message_id, client_phone,
        )

    async def resolve_notification_to_client(
        self, notification_message_id: str
    ) -> str | None:
        \"\"\"Resolve notification messageId to client phone.
        Returns None if mapping expired or doesn't exist.\"\"\"
        key = self._key("esc_msg", notification_message_id)
        return await self.client.get(key)
"""