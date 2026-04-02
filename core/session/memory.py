"""
Aleph Framework — Episodic Session Memory
==========================================
Two-tier conversation memory with compression.

Tier 1: Rolling raw window (last max_raw_turns turns in Redis list or in-memory)
Tier 2: Episodic summary (LLM-compressed older turns, stored as a single Redis key)

Compression triggers:
  Turn-based: window fills → compress oldest half → free space for new turns
  Time-based: user returns after gap_compression_hours → deep (aggressive) compress

Redis keys (aleph:{client_id}:):
  history:{phone}   — JSON list[{role, content}] — raw rolling window
  episodic:{phone}  — JSON {text, created_at, turn_count} — compressed summary
  last_turn:{phone} — float timestamp of last save_turn call

In-memory fallback (redis_client=None):
  _MEMORY_STORE[client_id][phone] = {history, summary, last_ts}
  Process-scoped. Not shared across processes. Dev/single-container only.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.session.memory")

# In-memory fallback store (no Redis mode)
# Structure: {client_id: {phone: {history: list, summary: str, last_ts: float}}}
_MEMORY_STORE: dict[str, dict[str, dict[str, Any]]] = {}

TURN_COMPRESSION_PROMPT = """\
You are a conversation memory compressor for a WhatsApp AI agent.
Summarize the conversation below into a structured briefing.

Required sections (omit section entirely if empty):
[Current Intent]: What the user is currently trying to accomplish
[Key Decisions]: Facts already established — do not re-confirm these
[Active Task]: Any ongoing multi-step process and its current state
[User Facts]: Relevant user details (name, location, preferences, constraints)
[Pending]: What was promised or left unanswered

Be concise and factual. Max 500 tokens. Omit small talk and pleasantries.\
"""

DEEP_COMPRESSION_PROMPT = """\
You are a conversation memory compressor. The user has been away for a long time.
Create a brief briefing for when they return.

Required sections (omit section entirely if empty):
[Last Intent]: What the user wanted before they left
[Active Task]: Any incomplete flow or task — preserve this precisely
[User Facts]: Key user details that remain relevant

Be very concise. Max 200 tokens. Drop all conversational details.\
"""


@dataclass
class MemoryContext:
    """Context retrieved from episodic memory for a single pipeline call."""

    raw_history: list[dict] = field(default_factory=list)
    summary: str = ""
    last_turn_ts: float = 0.0


class EpisodicMemory:
    """Episodic conversation memory with two-tier compression.

    Usage:
        mem = EpisodicMemory(config, redis_client=redis.client)
        ctx = await mem.get_context(phone)
        # ... run agent with ctx.raw_history and ctx.summary ...
        await mem.save_turn(phone, user_msg, assistant_msg)

    In-memory mode (redis_client=None):
        All state lives in the module-level _MEMORY_STORE dict.
        Lost on process restart. Not shared across workers.
    """

    def __init__(self, config: FrameworkConfig, redis_client: Any | None = None):
        self.config = config
        self.redis = redis_client
        self._prefix = f"aleph:{config.client_id}"
        self._cfg = config.sdk.sessions

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def get_context(self, phone: str) -> MemoryContext:
        """Load raw history + episodic summary for a conversation turn."""
        if self.redis is None:
            return self._mem_get_context(phone)
        return await self._redis_get_context(phone)

    async def save_turn(self, phone: str, user_msg: str, assistant_msg: str) -> None:
        """Persist a completed turn. Triggers turn-based compression if window fills."""
        turn = [
            {"role": "user", "content": user_msg},
            {"role": "assistant", "content": assistant_msg},
        ]
        if self.redis is None:
            self._mem_save_turn(phone, turn)
        else:
            await self._redis_save_turn(phone, turn)

    async def check_gap_compression(self, phone: str) -> None:
        """If user was away longer than gap_compression_hours, deep compress.
        No-op in in-memory mode (LLM compression requires Redis for persistence)."""
        if self.redis is None:
            return  # in-memory mode: gap compression not supported (no LLM call without persistence)

        raw = await self.redis.get(self._key("last_turn", phone))
        ts = float(raw) if raw else 0.0

        if ts == 0.0:
            return  # new user, nothing to compress

        hours_elapsed = (time.time() - ts) / 3600
        if hours_elapsed >= self._cfg.gap_compression_hours:
            logger.info(
                "Gap compression triggered for %s (%.1fh elapsed)", phone, hours_elapsed
            )
            await self._compress(phone, deep=True)

    # ------------------------------------------------------------------
    # In-memory backend
    # ------------------------------------------------------------------

    def _mem_store(self, phone: str) -> dict[str, Any]:
        """Get or create in-memory slot for this client+phone."""
        cid = self.config.client_id
        if cid not in _MEMORY_STORE:
            _MEMORY_STORE[cid] = {}
        if phone not in _MEMORY_STORE[cid]:
            _MEMORY_STORE[cid][phone] = {"history": [], "summary": "", "last_ts": 0.0}
        return _MEMORY_STORE[cid][phone]

    def _mem_get_context(self, phone: str) -> MemoryContext:
        slot = self._mem_store(phone)
        return MemoryContext(
            raw_history=list(slot["history"]),
            summary=slot["summary"],
            last_turn_ts=slot["last_ts"],
        )

    def _mem_save_turn(self, phone: str, turn: list[dict]) -> None:
        slot = self._mem_store(phone)
        slot["history"].extend(turn)
        slot["last_ts"] = time.time()
        # Check window — 2 messages per turn, max_raw_turns turns = max_raw_turns * 2 messages
        max_msgs = self._cfg.max_raw_turns * 2
        if len(slot["history"]) > max_msgs:
            # Compression is async — in-memory mode just truncates (no LLM available without config)
            overflow = slot["history"][: len(slot["history"]) - max_msgs]
            slot["history"] = slot["history"][len(slot["history"]) - max_msgs :]
            logger.debug("In-memory overflow truncation for %s: %d messages discarded", phone, len(overflow))
            # Best-effort: append overflow summary as plain text (no LLM call in sync context)
            if overflow:
                old = slot.get("summary", "")
                trimmed = " | ".join(m["content"][:60] for m in overflow)
                slot["summary"] = f"{old}\n[older]: {trimmed}".strip()

    # ------------------------------------------------------------------
    # Redis backend
    # ------------------------------------------------------------------

    def _key(self, kind: str, phone: str) -> str:
        return f"{self._prefix}:{kind}:{phone}"

    async def _redis_get_context(self, phone: str) -> MemoryContext:
        history_raw = await self.redis.get(self._key("history", phone))
        summary_raw = await self.redis.get(self._key("episodic", phone))
        ts_raw = await self.redis.get(self._key("last_turn", phone))

        history: list[dict] = json.loads(history_raw) if history_raw else []
        summary_obj: dict = json.loads(summary_raw) if summary_raw else {}
        summary_text: str = summary_obj.get("text", "")
        last_ts: float = float(ts_raw) if ts_raw else 0.0

        return MemoryContext(
            raw_history=history,
            summary=summary_text,
            last_turn_ts=last_ts,
        )

    async def _redis_save_turn(self, phone: str, turn: list[dict]) -> None:
        session_ttl = self._cfg.ttl
        summary_ttl = self._cfg.summary_ttl_days * 86400

        # Load current history
        raw = await self.redis.get(self._key("history", phone))
        history: list[dict] = json.loads(raw) if raw else []

        # Append new turn
        history.extend(turn)

        # Update last_turn timestamp
        now = time.time()
        await self.redis.set(self._key("last_turn", phone), str(now), ex=summary_ttl)

        # Check if compression needed
        max_msgs = self._cfg.max_raw_turns * 2
        if len(history) > max_msgs:
            # Compress oldest half before saving
            split = len(history) // 2
            to_compress = history[:split]
            history = history[split:]
            await self._compress_messages(phone, to_compress, deep=False)

        # Save trimmed history
        await self.redis.set(
            self._key("history", phone),
            json.dumps(history, ensure_ascii=False),
            ex=session_ttl,
        )

    # ------------------------------------------------------------------
    # Compression
    # ------------------------------------------------------------------

    async def _compress(self, phone: str, deep: bool) -> None:
        """Load full history + existing summary and compress them all."""
        raw = await self.redis.get(self._key("history", phone)) if self.redis else None
        history: list[dict] = json.loads(raw) if raw else []
        if not history:
            return
        await self._compress_messages(phone, history, deep=deep)
        # After deep compression, clear the raw window
        if deep and self.redis:
            await self.redis.delete(self._key("history", phone))
        elif deep:
            self._mem_store(phone)["history"] = []

    async def _compress_messages(
        self, phone: str, messages: list[dict], deep: bool
    ) -> None:
        """Call LLM and merge result into the stored episodic summary."""
        # Load existing summary to merge with
        if self.redis:
            raw = await self.redis.get(self._key("episodic", phone))
            existing: dict = json.loads(raw) if raw else {}
        else:
            existing = {"text": self._mem_store(phone).get("summary", ""), "turn_count": 0}

        existing_text = existing.get("text", "")
        turn_count = existing.get("turn_count", 0) + len(messages) // 2

        try:
            new_summary = await self._call_llm(messages, existing_text, deep)
        except Exception as e:
            logger.warning("Compression LLM call failed (%s), skipping", e)
            return

        summary_obj = {
            "text": new_summary,
            "created_at": time.time(),
            "turn_count": turn_count,
        }

        if self.redis:
            ttl = self._cfg.summary_ttl_days * 86400
            await self.redis.set(
                self._key("episodic", phone),
                json.dumps(summary_obj, ensure_ascii=False),
                ex=ttl,
            )
        else:
            self._mem_store(phone)["summary"] = new_summary

        label = "deep" if deep else "turn-based"
        logger.info(
            "Episodic compression (%s) for %s: %d msgs → %d chars summary",
            label,
            phone,
            len(messages),
            len(new_summary),
        )

    async def _call_llm(
        self, messages: list[dict], existing_summary: str, deep: bool
    ) -> str:
        """Call LLM to produce compressed summary. Uses compression_model → fallback → primary."""
        from core.llm.llm_router import _create_openai_client  # lazy: circular import prevention

        compression_model = (
            self._cfg.compression_model
            or self.config.agent.fallback_model
            or self.config.agent.model
        )
        prompt = DEEP_COMPRESSION_PROMPT if deep else TURN_COMPRESSION_PROMPT
        max_tokens = 250 if deep else 600

        # Build input: existing summary + messages to compress
        history_text = "\n".join(f"{m['role'].upper()}: {m['content']}" for m in messages)
        if existing_summary:
            user_content = (
                f"EXISTING SUMMARY:\n{existing_summary}\n\nNEW CONVERSATION TO INTEGRATE:\n{history_text}"
            )
        else:
            user_content = history_text

        client = _create_openai_client(self.config, timeout=30)
        response = await client.chat.completions.create(
            model=compression_model,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_content},
            ],
            max_tokens=max_tokens,
            temperature=0.3,
        )
        return response.choices[0].message.content or ""
