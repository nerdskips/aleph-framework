"""
Aleph Framework — Redis Session Manager
================================================
Manages WhatsApp-specific state in Redis:
  - Message buffer (chunked messages consolidation)
  - Anti-spam (messageId dedup)
  - Processing lock (per phone, prevents duplicate responses)
  - Client context (name, neighborhood, preferences)

All keys are prefixed with aleph:{client_id}: for multi-agent isolation
on shared Redis.

Environment:
  REDIS_URL — Redis connection string (e.g. redis://:pass@host:port/0)
"""

from __future__ import annotations

import json
import logging
import os
import time

import redis.asyncio as aioredis

from core.registry.schema import FrameworkConfig
from core.session.redis_escalation import EscalationData

logger = logging.getLogger("aleph.session")


class RedisSession:
    """Redis session manager for a single agent instance.

    All operations are async. Keys are automatically prefixed
    with aleph:{client_id}: for isolation.
    """

    def __init__(self, config: FrameworkConfig):
        self.config = config
        self.prefix = f"aleph:{config.client_id}"
        self._client: aioredis.Redis | None = None

    async def connect(self) -> None:
        """Connect to Redis using REDIS_URL from environment."""
        url = os.environ.get("REDIS_URL")
        if not url:
            raise ValueError("REDIS_URL environment variable not set")

        self._client = aioredis.from_url(
            url,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        # Test connection
        await self._client.ping()
        logger.info("Redis connected: %s (prefix: %s)", url.split("@")[-1], self.prefix)

    async def close(self) -> None:
        """Close Redis connection."""
        if self._client:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> aioredis.Redis:
        if not self._client:
            raise RuntimeError("Redis not connected. Call connect() first.")
        return self._client

    def _key(self, *parts: str) -> str:
        """Build a prefixed Redis key: aleph:{client_id}:{parts}"""
        return ":".join([self.prefix, *parts])

    # -------------------------------------------------------------------
    # Anti-spam — messageId dedup
    # -------------------------------------------------------------------

    async def is_duplicate(self, message_id: str) -> bool:
        """Check if messageId was already processed (anti-spam).
        Returns True if duplicate (should be ignored)."""
        key = self._key("spam", message_id)
        was_set = await self.client.set(
            key, "1",
            nx=True,  # only set if not exists
            ex=self.config.session.antispam_ttl,
        )
        if not was_set:
            logger.debug("Duplicate messageId blocked: %s", message_id)
            return True
        return False

    # -------------------------------------------------------------------
    # Processing lock — per phone
    # -------------------------------------------------------------------

    async def acquire_lock(self, phone: str) -> bool:
        """Acquire processing lock for a phone number.
        Returns True if lock acquired, False if already locked."""
        key = self._key("lock", phone)
        acquired = await self.client.set(
            key, str(time.time()),
            nx=True,
            ex=self.config.session.processing_lock_ttl,
        )
        if acquired:
            logger.debug("Lock acquired: %s", phone)
        else:
            logger.debug("Lock busy: %s", phone)
        return bool(acquired)

    async def release_lock(self, phone: str) -> None:
        """Release processing lock for a phone number."""
        key = self._key("lock", phone)
        await self.client.delete(key)
        logger.debug("Lock released: %s", phone)

    # -------------------------------------------------------------------
    # Message buffer — chunked messages consolidation
    # -------------------------------------------------------------------

    async def buffer_message(self, phone: str, text: str) -> None:
        """Add a message chunk to the buffer for consolidation."""
        key = self._key("buffer", phone)
        entry = json.dumps({"text": text, "ts": time.time()})
        await self.client.rpush(key, entry)
        await self.client.expire(key, self.config.session.buffer_timeout + 5)
        logger.debug("Buffered message for %s: %d chars", phone, len(text))

    async def consume_buffer(self, phone: str) -> str:
        """Consume all buffered messages for a phone.
        Returns consolidated text (messages joined with newline)."""
        key = self._key("buffer", phone)
        messages = await self.client.lrange(key, 0, -1)
        await self.client.delete(key)

        if not messages:
            return ""

        texts = []
        for raw in messages:
            try:
                entry = json.loads(raw)
                texts.append(entry["text"])
            except (json.JSONDecodeError, KeyError):
                texts.append(str(raw))

        consolidated = "\n".join(texts)
        logger.debug(
            "Buffer consumed for %s: %d messages → %d chars",
            phone, len(messages), len(consolidated),
        )
        return consolidated

    async def has_buffer(self, phone: str) -> bool:
        """Check if there are buffered messages waiting."""
        key = self._key("buffer", phone)
        length = await self.client.llen(key)
        return length > 0

    # -------------------------------------------------------------------
    # Client context — persistent per-client metadata
    # -------------------------------------------------------------------

    async def save_context(self, phone: str, data: dict) -> None:
        """Save/merge client context (name, neighborhood, etc).
        Merges with existing context (doesn't overwrite)."""
        key = self._key("ctx", phone)
        existing = await self.get_context(phone)
        existing.update(data)
        await self.client.set(
            key,
            json.dumps(existing, ensure_ascii=False),
            ex=self.config.session.context_ttl,
        )
        logger.debug("Context saved for %s: %s", phone, list(data.keys()))

    async def get_context(self, phone: str) -> dict:
        """Get client context for a phone number."""
        key = self._key("ctx", phone)
        raw = await self.client.get(key)
        if not raw:
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {}

    # -------------------------------------------------------------------
    # Transbordo — takeover lock
    # -------------------------------------------------------------------

    async def is_takeover_active(self, phone: str) -> bool:
        """Check if a human takeover is active for this phone."""
        key = self._key("takeover", phone)
        return bool(await self.client.exists(key))

    async def activate_takeover(self, phone: str) -> None:
        """Activate human takeover lock."""
        key = self._key("takeover", phone)
        ttl = self.config.human.takeover_lock_ttl
        await self.client.set(key, str(time.time()), ex=ttl)
        logger.info("Takeover activated for %s (TTL: %ds)", phone, ttl)

    async def renew_takeover(self, phone: str) -> None:
        """Renew takeover lock TTL (on each human message)."""
        key = self._key("takeover", phone)
        ttl = self.config.human.takeover_lock_ttl
        await self.client.expire(key, ttl)
        logger.debug("Takeover renewed for %s", phone)

    async def release_takeover(self, phone: str) -> None:
        """Release human takeover."""
        key = self._key("takeover", phone)
        await self.client.delete(key)
        logger.info("Takeover released for %s", phone)

    # -------------------------------------------------------------------
    # LID mapping — WhatsApp LID ↔ phone
    # -------------------------------------------------------------------

    async def set_lid_mapping(self, lid: str, phone: str) -> None:
        """Store LID → phone mapping."""
        key = self._key("lid", lid)
        ttl = self.config.human.lid_mapping_ttl
        await self.client.set(key, phone, ex=ttl)

    async def resolve_lid(self, lid: str) -> str | None:
        """Resolve LID to phone number."""
        key = self._key("lid", lid)
        return await self.client.get(key)

    # -------------------------------------------------------------------
    # Escalation — pause + resume flow
    # -------------------------------------------------------------------

    async def save_escalation(self, data: EscalationData) -> None:
        """Save escalation session for a client phone."""
        key = self._key("esc", data.client_phone)
        ttl = self.config.human.escalation_session_ttl
        await self.client.set(key, data.to_json(), ex=ttl)
        logger.info(
            "Escalation saved for %s → responsible %s (TTL: %ds)",
            data.client_phone, data.responsible_phone, ttl,
        )

    async def get_escalation(self, client_phone: str) -> EscalationData | None:
        """Load escalation session for a client phone."""
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
        """Clear escalation session after human resolves it."""
        key = self._key("esc", client_phone)
        await self.client.delete(key)
        logger.info("Escalation cleared for %s", client_phone)

    async def is_escalation_active(self, client_phone: str) -> bool:
        """Check if there's an active escalation for this phone."""
        key = self._key("esc", client_phone)
        return bool(await self.client.exists(key))

    async def map_notification_to_client(
        self, notification_message_id: str, client_phone: str
    ) -> None:
        """Map notification messageId to client phone for quote lookup."""
        key = self._key("esc_msg", notification_message_id)
        ttl = self.config.human.escalation_session_ttl
        await self.client.set(key, client_phone, ex=ttl)
        logger.debug(
            "Notification mapped: %s → %s", notification_message_id, client_phone,
        )

    async def resolve_notification_to_client(
        self, notification_message_id: str
    ) -> str | None:
        """Resolve notification messageId to client phone."""
        key = self._key("esc_msg", notification_message_id)
        return await self.client.get(key)

    # -------------------------------------------------------------------
    # Flow state — declarative state machine (Phase 8)
    # -------------------------------------------------------------------

    async def get_flow_state(self, phone: str):
        """Load active flow state for a phone number.
        Returns FlowState or None if no flow is active or TTL expired."""
        from core.flows.state import FlowState

        key = self._key("flow", phone)
        raw = await self.client.get(key)
        if not raw:
            return None
        try:
            return FlowState.from_json(raw)
        except (json.JSONDecodeError, KeyError) as e:
            logger.error("Failed to parse flow state for %s: %s", phone, e)
            return None

    async def set_flow_state(self, phone: str, state, ttl: int) -> None:
        """Persist flow state for a phone number with TTL."""
        key = self._key("flow", phone)
        await self.client.set(key, state.to_json(), ex=ttl)
        logger.debug("Flow state saved for %s (flow=%s step=%s TTL=%ds)",
                     phone, state.flow_id, state.step_id, ttl)

    async def clear_flow_state(self, phone: str) -> None:
        """Delete flow state when a flow completes or is abandoned."""
        key = self._key("flow", phone)
        await self.client.delete(key)
        logger.debug("Flow state cleared for %s", phone)
