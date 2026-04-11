# Episodic Memory + LLM Router + Self-Awareness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add bounded episodic conversation memory with two-tier compression (turn-based + time-based), rename the LLM routing module for discoverability, and add self-awareness context injection so agents can recover from interrupted flows.

**Architecture:** EpisodicMemory lives in `core/session/memory.py` and manages two Redis keys per user — a rolling raw history window and a compressed episodic summary. When the window fills, the oldest half is compressed into the summary via a cheap LLM call (fire-and-forget, not in the critical path). A gap trigger compresses aggressively when the user returns after N hours. Pipeline injects the summary into system instructions before the LLM run — not into conversation history. `core/llm/bifrost.py` is renamed `core/llm/llm_router.py` for discoverability. Self-awareness reads the Phase 12 summary key plus flow/escalation state and injects structured context when relevance gates pass.

**Tech Stack:** Python 3.12, aioredis, OpenAI-compatible AsyncOpenAI client, Pydantic v2, pytest-asyncio

---

## File Map

### Phase 12 — Episodic Session Memory

| Action | File | Responsibility |
|---|---|---|
| CREATE | `core/session/memory.py` | EpisodicMemory class — rolling window + summary, Redis + in-memory backends |
| MODIFY | `core/registry/schema.py` | Add 4 fields to `SDKSessionsConfig` |
| MODIFY | `core/engine/pipeline.py` | Accept + use EpisodicMemory, inject summary into instructions |
| MODIFY | `core/engine/runner.py` | Pass raw history to Runner.run(), inject summary prefix into instructions |
| MODIFY | `core/api/webhooks.py` | Boot EpisodicMemory at startup, pass to process_message |
| CREATE | `tests/framework/test_episodic_memory.py` | Full coverage — both backends, both compression triggers |

### Phase 13 — LLM-Agnostic Router

| Action | File | Responsibility |
|---|---|---|
| CREATE | `core/llm/llm_router.py` | Copy of bifrost.py — renamed, docstring updated |
| DELETE | `core/llm/bifrost.py` | Replaced by llm_router.py |
| MODIFY | `core/llm/__init__.py` | Update exports to point at llm_router |
| MODIFY | `core/engine/runner.py` | Update import from bifrost → llm_router |
| MODIFY | `core/engine/pipeline.py` | Update import if present |
| MODIFY | `core/mcp/server.py` | Update import if present |
| MODIFY | `CLAUDE.md` | Update module path reference |
| MODIFY | `README.md` | Update project structure entry |

### Phase 14 — Self-Awareness

| Action | File | Responsibility |
|---|---|---|
| CREATE | `core/awareness/__init__.py` | Module exports |
| CREATE | `core/awareness/reader.py` | Read + interpret Redis state (flow, escalation, episodic summary) |
| CREATE | `core/awareness/injector.py` | Relevance gates + format structured context string |
| MODIFY | `core/registry/schema.py` | Add `SelfAwarenessConfig` + field on `FrameworkConfig` |
| MODIFY | `core/engine/pipeline.py` | Add awareness injection step after knowledge search |
| CREATE | `tests/framework/test_awareness.py` | Relevance gates, injection format, full pipeline integration |

---

## Phase 12 — Episodic Session Memory

---

### Task 1: Schema additions

**Files:**
- Modify: `core/registry/schema.py` — `SDKSessionsConfig` class (around line 160)
- Test: `tests/framework/test_episodic_memory.py`

- [ ] **Step 1: Write the failing schema test**

```python
"""Tests: Phase 12 — Episodic Session Memory schema and behavior."""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.registry.schema import FrameworkConfig, SDKSessionsConfig


def _make_config(**session_overrides) -> FrameworkConfig:
    sdk = {"sessions": {"max_raw_turns": 4, **session_overrides}}
    return FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"}, sdk=sdk)


def test_sessions_default_max_raw_turns():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.max_raw_turns == 8

def test_sessions_default_compression_model_empty():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.compression_model == ""

def test_sessions_default_gap_compression_hours():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.gap_compression_hours == 3.0

def test_sessions_default_summary_ttl_days():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.sdk.sessions.summary_ttl_days == 30

def test_sessions_custom_values():
    config = _make_config(max_raw_turns=6, gap_compression_hours=1.5, summary_ttl_days=7)
    assert config.sdk.sessions.max_raw_turns == 6
    assert config.sdk.sessions.gap_compression_hours == 1.5
    assert config.sdk.sessions.summary_ttl_days == 7
```

- [ ] **Step 2: Run to confirm failure**

```bash
cd /root/zuper-framework && .venv/bin/pytest tests/framework/test_episodic_memory.py::test_sessions_default_max_raw_turns -v
```
Expected: `FAILED — AttributeError: max_raw_turns`

- [ ] **Step 3: Add fields to SDKSessionsConfig in schema.py**

Find `SDKSessionsConfig` (line ~160) and add four fields after `ttl`:

```python
class SDKSessionsConfig(BaseModel):
    """SDK 0.12 native Sessions with Redis.
    Replaces manual history management from session.py."""
    enabled: bool = Field(True, description="Use SDK native Redis sessions for conversation history")
    redis_key_prefix: str = Field("aleph:session:", description="Redis key prefix for SDK sessions")
    history_limit: int = Field(50, ge=5, description="Max messages kept in SDK session history")
    ttl: int = Field(10800, ge=60, description="Session TTL in seconds (default 3h)")
    # Episodic memory — Phase 12
    max_raw_turns: int = Field(8, ge=2, le=50, description="Rolling raw turn window. Compression fires when full.")
    compression_model: str = Field("", description="Model for compression. Empty = fallback_model → agent.model")
    gap_compression_hours: float = Field(3.0, ge=0.5, description="Hours of inactivity before deep (aggressive) compression")
    summary_ttl_days: int = Field(30, ge=1, description="Days to retain episodic summary before expiry")
```

- [ ] **Step 4: Run schema tests**

```bash
.venv/bin/pytest tests/framework/test_episodic_memory.py -v -k "schema or default or custom"
```
Expected: 5 passed

- [ ] **Step 5: Confirm no regressions**

```bash
.venv/bin/pytest tests/ -q
```
Expected: 65 passed, 2 pre-existing failures

- [ ] **Step 6: Commit**

```bash
rtk git add core/registry/schema.py tests/framework/test_episodic_memory.py
git commit -m "feat(memory): add episodic memory schema fields to SDKSessionsConfig"
```

---

### Task 2: EpisodicMemory — in-memory backend + data model

**Files:**
- Create: `core/session/memory.py`
- Test: `tests/framework/test_episodic_memory.py`

- [ ] **Step 1: Write failing tests for in-memory backend**

Add to `tests/framework/test_episodic_memory.py`:

```python
from core.session.memory import EpisodicMemory, MemoryContext


def _make_memory(max_raw_turns: int = 4) -> EpisodicMemory:
    config = _make_config(max_raw_turns=max_raw_turns)
    return EpisodicMemory(config, redis_client=None)  # in-memory mode


async def test_get_context_empty_user():
    mem = _make_memory()
    ctx = await mem.get_context("+5511999")
    assert isinstance(ctx, MemoryContext)
    assert ctx.raw_history == []
    assert ctx.summary == ""
    assert ctx.last_turn_ts == 0.0


async def test_save_turn_adds_two_messages():
    mem = _make_memory()
    await mem.save_turn("+5511999", "oi", "olá")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) == 2
    assert ctx.raw_history[0] == {"role": "user", "content": "oi"}
    assert ctx.raw_history[1] == {"role": "assistant", "content": "olá"}


async def test_multiple_turns_accumulate():
    mem = _make_memory(max_raw_turns=8)
    await mem.save_turn("+5511999", "msg1", "resp1")
    await mem.save_turn("+5511999", "msg2", "resp2")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) == 4


async def test_last_turn_ts_updated():
    mem = _make_memory()
    before = time.time()
    await mem.save_turn("+5511999", "oi", "olá")
    ctx = await mem.get_context("+5511999")
    assert ctx.last_turn_ts >= before


async def test_different_phones_isolated():
    mem = _make_memory()
    await mem.save_turn("+5511111", "a", "b")
    await mem.save_turn("+5522222", "c", "d")
    ctx1 = await mem.get_context("+5511111")
    ctx2 = await mem.get_context("+5522222")
    assert ctx1.raw_history[0]["content"] == "a"
    assert ctx2.raw_history[0]["content"] == "c"
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/framework/test_episodic_memory.py -v -k "context or turn or phone"
```
Expected: `ImportError: cannot import name 'EpisodicMemory'`

- [ ] **Step 3: Create `core/session/memory.py`**

```python
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
        """If user was away longer than gap_compression_hours, deep compress."""
        if self.redis is None:
            ts = self._mem_store(phone).get("last_ts", 0.0)
        else:
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
            label, phone, len(messages), len(new_summary),
        )

    async def _call_llm(
        self, messages: list[dict], existing_summary: str, deep: bool
    ) -> str:
        """Call LLM to produce compressed summary. Uses compression_model → fallback → primary."""
        from openai import AsyncOpenAI
        from core.llm.llm_router import _create_openai_client  # Phase 13 rename

        compression_model = (
            self._cfg.compression_model
            or self.config.agent.fallback_model
            or self.config.agent.model
        )
        prompt = DEEP_COMPRESSION_PROMPT if deep else TURN_COMPRESSION_PROMPT
        max_tokens = 250 if deep else 600

        # Build input: existing summary + messages to compress
        history_text = "\n".join(
            f"{m['role'].upper()}: {m['content']}" for m in messages
        )
        if existing_summary:
            user_content = f"EXISTING SUMMARY:\n{existing_summary}\n\nNEW CONVERSATION TO INTEGRATE:\n{history_text}"
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
```

- [ ] **Step 4: Run in-memory tests**

```bash
.venv/bin/pytest tests/framework/test_episodic_memory.py -v -k "context or turn or phone"
```
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
rtk git add core/session/memory.py tests/framework/test_episodic_memory.py
git commit -m "feat(memory): EpisodicMemory class with in-memory backend and compression skeleton"
```

---

### Task 3: Redis backend tests + compression tests

**Files:**
- Test: `tests/framework/test_episodic_memory.py`

- [ ] **Step 1: Add Redis backend + compression tests**

```python
def _make_redis_mock() -> AsyncMock:
    redis = AsyncMock()
    _store: dict[str, str] = {}

    async def mock_get(key):
        return _store.get(key)

    async def mock_set(key, value, ex=None):
        _store[key] = value

    async def mock_delete(key):
        _store.pop(key, None)

    redis.get = mock_get
    redis.set = mock_set
    redis.delete = mock_delete
    return redis


def _make_redis_memory(max_raw_turns: int = 4) -> EpisodicMemory:
    config = _make_config(max_raw_turns=max_raw_turns)
    return EpisodicMemory(config, redis_client=_make_redis_mock())


async def test_redis_get_context_empty():
    mem = _make_redis_memory()
    ctx = await mem.get_context("+5511999")
    assert ctx.raw_history == []
    assert ctx.summary == ""


async def test_redis_save_and_retrieve_turn():
    mem = _make_redis_memory()
    await mem.save_turn("+5511999", "oi", "olá")
    ctx = await mem.get_context("+5511999")
    assert len(ctx.raw_history) == 2
    assert ctx.raw_history[0]["role"] == "user"
    assert ctx.raw_history[1]["role"] == "assistant"


async def test_redis_window_triggers_compression():
    """When turns exceed max_raw_turns, oldest half is removed from raw history."""
    mem = _make_redis_memory(max_raw_turns=2)  # window = 2 turns = 4 messages
    # Fill window exactly
    await mem.save_turn("+5511999", "msg1", "resp1")
    await mem.save_turn("+5511999", "msg2", "resp2")
    # This turn overflows — triggers compression of oldest 2 messages
    with patch.object(mem, "_compress_messages", new_callable=AsyncMock) as mock_compress:
        await mem.save_turn("+5511999", "msg3", "resp3")
        mock_compress.assert_called_once()

    ctx = await mem.get_context("+5511999")
    # After compression, raw history should have at most max_raw_turns * 2 messages
    assert len(ctx.raw_history) <= 4


async def test_gap_compression_triggers_when_old():
    mem = _make_redis_memory()
    # Simulate last turn 4 hours ago
    mem.redis.get = AsyncMock(return_value=str(time.time() - 4 * 3600))
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_called_once_with("+5511999", deep=True)


async def test_gap_compression_skips_when_recent():
    mem = _make_redis_memory()
    mem.redis.get = AsyncMock(return_value=str(time.time() - 30 * 60))  # 30 min ago
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_not_called()


async def test_gap_compression_skips_new_user():
    mem = _make_redis_memory()
    mem.redis.get = AsyncMock(return_value=None)  # new user
    with patch.object(mem, "_compress", new_callable=AsyncMock) as mock_compress:
        await mem.check_gap_compression("+5511999")
        mock_compress.assert_not_called()
```

- [ ] **Step 2: Run**

```bash
.venv/bin/pytest tests/framework/test_episodic_memory.py -v
```
Expected: all passing

- [ ] **Step 3: Commit**

```bash
rtk git add tests/framework/test_episodic_memory.py
git commit -m "test(memory): Redis backend and compression trigger coverage"
```

---

### Task 4: Wire EpisodicMemory into pipeline + webhooks

**Files:**
- Modify: `core/engine/pipeline.py`
- Modify: `core/engine/runner.py`
- Modify: `core/api/webhooks.py`

- [ ] **Step 1: Add `episodic_memory` parameter to `process_message` in pipeline.py**

Find `async def process_message(` and add the parameter + gap check + summary injection:

```python
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
```

At the very start of the function body (before guardrail check), add:

```python
    # Episodic memory — check gap + load context
    memory_ctx = None
    if episodic_memory is not None and phone:
        try:
            await episodic_memory.check_gap_compression(phone)
            memory_ctx = await episodic_memory.get_context(phone)
        except Exception as e:
            logger.warning("Episodic memory load failed (non-fatal): %s", e)
```

After the final `result = await run_agent(...)` call (both primary and fallback), add turn save:

```python
    # Save completed turn to episodic memory (fire-and-forget)
    if episodic_memory is not None and phone and result.response:
        try:
            await episodic_memory.save_turn(phone, user_message, result.response)
        except Exception as e:
            logger.warning("Episodic memory save failed (non-fatal): %s", e)
```

Pass `memory_ctx` to `run_agent()` by adding it to the call:

```python
    result = await run_agent(
        registry=registry,
        user_message=user_message,
        message_history=message_history,
        memory_ctx=memory_ctx,        # ← NEW
    )
```

- [ ] **Step 2: Update `run_agent` in runner.py to accept and use memory_ctx**

Find `async def run_agent(` and add parameter:

```python
async def run_agent(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
    memory_ctx=None,                  # ← NEW: MemoryContext | None
) -> AgentResult:
```

In `run_agent`, before building `input_messages`, add summary injection into instructions:

```python
    config = registry.config
    model_settings = create_model_settings(config)

    # Inject episodic summary into system instructions (not history)
    extra_instructions = ""
    if memory_ctx is not None and memory_ctx.summary:
        extra_instructions = f"\n\n[Contexto de conversas anteriores]\n{memory_ctx.summary}"

    # Use raw history from memory if available, otherwise use passed history
    effective_history = (
        memory_ctx.raw_history
        if memory_ctx is not None and memory_ctx.raw_history
        else (message_history or [])
    )

    input_messages = list(effective_history)
    input_messages.append({"role": "user", "content": user_message})
```

In `build_agent()`, pass `extra_instructions` to the agent by adding to the call:

```python
    agent = build_agent(registry, primary_model, model_settings, extra_instructions=extra_instructions)
```

Update `build_agent` signature and body:

```python
def build_agent(
    registry: AgentRegistry,
    model: Any,
    model_settings: ModelSettings,
    extra_instructions: str = "",      # ← NEW
) -> Agent:
    ...
    instructions = registry.system_prompt
    ...  # existing TZ injection
    if extra_instructions:
        instructions = f"{instructions}{extra_instructions}"
    ...
```

- [ ] **Step 3: Boot EpisodicMemory in webhooks.py**

Add global at top (with other globals):
```python
_memory: EpisodicMemory | None = None
```

Add import at top:
```python
from core.session.memory import EpisodicMemory
```

In `lifespan()`, after Redis connect:
```python
    # Boot EpisodicMemory (always — falls back to in-memory if Redis unavailable)
    redis_client = _redis.client if _redis else None
    _memory = EpisodicMemory(_registry.config, redis_client=redis_client)
    logger.info("EpisodicMemory initialized (redis=%s)", redis_client is not None)
```

In the `process_message()` call:
```python
            result = await process_message(
                registry=_registry,
                user_message=consolidated,
                phone=phone,
                redis_session=_redis,
                sender=_sender,
                habits_db=_habits_db,
                knowledge_db=_knowledge_db,
                flow_engine=_flow_engine,
                episodic_memory=_memory,     # ← NEW
            )
```

- [ ] **Step 4: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```
Expected: 65 passed, 2 pre-existing failures

- [ ] **Step 5: Commit**

```bash
rtk git add core/engine/pipeline.py core/engine/runner.py core/api/webhooks.py
git commit -m "feat(memory): wire EpisodicMemory into pipeline, runner, and webhook boot"
```

---

## Phase 13 — LLM-Agnostic Router

---

### Task 5: Rename bifrost.py → llm_router.py

**Files:**
- Create: `core/llm/llm_router.py`
- Delete: `core/llm/bifrost.py`
- Modify: `core/llm/__init__.py`, `core/engine/runner.py`, `core/mcp/server.py`, `CLAUDE.md`, `README.md`

- [ ] **Step 1: Create llm_router.py as renamed copy**

Copy `core/llm/bifrost.py` content to `core/llm/llm_router.py` with updated docstring header:

```python
"""
Aleph Framework — LLM Router
==============================
Routes LLM calls to any OpenAI-compatible provider.

Supports Bifrost (recommended gateway), direct provider keys, and custom endpoints.
Provider is selected via LLM_PROVIDER env var or config.llm.provider.

Supported providers (LLM_PROVIDER value → env vars needed):
  bifrost    → BIFROST_URL + BIFROST_API_KEY     (default, routes to any model)
  openai     → OPENAI_API_KEY
  gemini     → GEMINI_API_KEY
  deepseek   → DEEPSEEK_API_KEY
  openrouter → OPENROUTER_API_KEY
  custom     → LLM_BASE_URL + LLM_API_KEY        (any OpenAI-compatible endpoint)

All other code is unchanged from the provider resolution and model factory.
"""
```

Keep all functions identical. Only the module docstring and filename change.

Change logger name:
```python
logger = logging.getLogger("aleph.llm.router")
```

- [ ] **Step 2: Update `core/llm/__init__.py`**

```python
"""Aleph Framework — LLM gateway (provider-agnostic router)."""

from __future__ import annotations

from core.llm.llm_router import (
    create_primary_model,
    create_fallback_model,
    create_model_settings,
)

__all__ = ["create_primary_model", "create_fallback_model", "create_model_settings"]
```

- [ ] **Step 3: Update imports in all consumers**

In `core/engine/runner.py`, change:
```python
from core.llm.bifrost import (        # old
from core.llm.llm_router import (     # new
```

In `core/session/memory.py` (Phase 12), the `_call_llm` already references `llm_router` — confirm correct.

Search for any remaining bifrost references:
```bash
grep -r "bifrost" /root/zuper-framework/core/ --include="*.py" -l
```
Update any found files.

- [ ] **Step 4: Delete bifrost.py**

```bash
git rm core/llm/bifrost.py
```

- [ ] **Step 5: Update CLAUDE.md**

Find the Key Modules table entry for `core/llm/bifrost.py` and change to:
```
| `core/llm/llm_router.py` | Provider-agnostic LLM routing + fallback |
```

- [ ] **Step 6: Update README.md project structure**

Find `bifrost.py` in the project structure tree and change to:
```
      llm_router.py    # Provider-agnostic LLM routing + fallback (Bifrost, OpenAI, Gemini…)
```

- [ ] **Step 7: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```
Expected: 65 passed, 2 pre-existing failures

- [ ] **Step 8: Lint**

```bash
ruff check core/ tests/
```
Expected: no new errors

- [ ] **Step 9: Commit**

```bash
rtk git add core/llm/llm_router.py core/llm/__init__.py core/engine/runner.py CLAUDE.md README.md
git commit -m "refactor(llm): rename bifrost.py → llm_router.py for discoverability"
```

---

## Phase 14 — Self-Awareness (Simplified by Phase 12)

---

### Task 6: SelfAwarenessConfig schema

**Files:**
- Modify: `core/registry/schema.py`
- Test: `tests/framework/test_awareness.py`

- [ ] **Step 1: Write failing schema tests**

```python
"""Tests: Phase 14 — Self-Awareness context injection."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from core.registry.schema import FrameworkConfig


def _make_config(**awareness_overrides) -> FrameworkConfig:
    return FrameworkConfig(
        client_id="test",
        agent={"name": "Bot", "model": "gpt-4o-mini"},
        self_awareness=awareness_overrides or {},
    )


def test_self_awareness_default_off():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.self_awareness.enabled is False


def test_self_awareness_default_return_gap():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.self_awareness.return_gap_minutes == 30


def test_self_awareness_default_max_age():
    config = FrameworkConfig(client_id="test", agent={"name": "Bot", "model": "gpt-4o-mini"})
    assert config.self_awareness.max_injection_age_hours == 4.0


def test_self_awareness_can_be_enabled():
    config = _make_config(enabled=True)
    assert config.self_awareness.enabled is True
```

- [ ] **Step 2: Run to confirm failure**

```bash
.venv/bin/pytest tests/framework/test_awareness.py -v -k "schema or default"
```
Expected: `FAILED — ValidationError: self_awareness field not found`

- [ ] **Step 3: Add SelfAwarenessConfig to schema.py**

Add new class before `FrameworkConfig`:

```python
class SelfAwarenessConfig(BaseModel):
    """Agent self-awareness — inject prior state context before LLM run.
    DEFAULT OFF. Reads episodic summary + flow/escalation state from Redis.
    Only injects when relevance gates pass (gap + age checks)."""
    enabled: bool = Field(False, description="DEFAULT OFF — inject prior state context")
    return_gap_minutes: float = Field(30.0, ge=1.0, description="Min inactivity gap (minutes) before injection fires")
    max_injection_age_hours: float = Field(4.0, ge=0.5, description="States older than this are not injected")
    include_flow: bool = Field(True, description="Include interrupted flow state in injection")
    include_escalation: bool = Field(True, description="Include escalation state in injection")
    include_summary: bool = Field(True, description="Include episodic summary in injection")
```

Add field to `FrameworkConfig`:

```python
    # Self-awareness — prior state injection (DEFAULT OFF)
    self_awareness: SelfAwarenessConfig = Field(default_factory=SelfAwarenessConfig)
```

- [ ] **Step 4: Run schema tests**

```bash
.venv/bin/pytest tests/framework/test_awareness.py -v -k "schema or default"
```
Expected: 4 passed

- [ ] **Step 5: Commit**

```bash
rtk git add core/registry/schema.py tests/framework/test_awareness.py
git commit -m "feat(awareness): add SelfAwarenessConfig schema (DEFAULT OFF)"
```

---

### Task 7: Awareness reader + injector

**Files:**
- Create: `core/awareness/__init__.py`
- Create: `core/awareness/reader.py`
- Create: `core/awareness/injector.py`

- [ ] **Step 1: Add reader + injector tests**

```python
import time
from core.awareness.reader import AwarenessState, build_awareness_state
from core.awareness.injector import build_injection, should_inject


def _mock_memory_ctx(summary="", last_ts=0.0):
    from core.session.memory import MemoryContext
    return MemoryContext(raw_history=[], summary=summary, last_turn_ts=last_ts)


def _mock_flow_state(flow_id="checkout", step_id="address", elapsed_hours=0.5):
    state = MagicMock()
    state.flow_id = flow_id
    state.step_id = step_id
    state.updated_at = time.time() - elapsed_hours * 3600
    return state


async def test_no_injection_for_new_user():
    config = _make_config(enabled=True)
    state = await build_awareness_state(
        config=config,
        memory_ctx=_mock_memory_ctx(),
        flow_state=None,
        escalation=None,
    )
    assert should_inject(config.self_awareness, state) is False


async def test_no_injection_when_recent_user():
    config = _make_config(enabled=True, return_gap_minutes=30)
    # User was active 5 minutes ago
    ctx = _mock_memory_ctx(summary="some context", last_ts=time.time() - 5 * 60)
    state = await build_awareness_state(config=config, memory_ctx=ctx, flow_state=None, escalation=None)
    assert should_inject(config.self_awareness, state) is False


async def test_injection_fires_after_gap():
    config = _make_config(enabled=True, return_gap_minutes=30)
    ctx = _mock_memory_ctx(summary="User wants delivery on Saturday", last_ts=time.time() - 45 * 60)
    state = await build_awareness_state(config=config, memory_ctx=ctx, flow_state=None, escalation=None)
    assert should_inject(config.self_awareness, state) is True


async def test_no_injection_when_state_too_old():
    config = _make_config(enabled=True, return_gap_minutes=30, max_injection_age_hours=4)
    # Last turn 6 hours ago — older than max_injection_age_hours
    ctx = _mock_memory_ctx(summary="old context", last_ts=time.time() - 6 * 3600)
    state = await build_awareness_state(config=config, memory_ctx=ctx, flow_state=None, escalation=None)
    assert should_inject(config.self_awareness, state) is False


def test_build_injection_includes_summary():
    config = _make_config(enabled=True)
    state = AwarenessState(
        summary="User wants delivery on Saturday",
        flow_id=None,
        flow_step=None,
        escalation_active=False,
        elapsed_minutes=45.0,
    )
    text = build_injection(config.self_awareness, state)
    assert "Saturday" in text
    assert text.strip() != ""


def test_build_injection_includes_flow():
    config = _make_config(enabled=True)
    state = AwarenessState(
        summary="",
        flow_id="checkout",
        flow_step="waiting_address",
        escalation_active=False,
        elapsed_minutes=45.0,
    )
    text = build_injection(config.self_awareness, state)
    assert "checkout" in text
    assert "waiting_address" in text
```

- [ ] **Step 2: Create `core/awareness/reader.py`**

```python
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
```

- [ ] **Step 3: Create `core/awareness/injector.py`**

```python
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
```

- [ ] **Step 4: Create `core/awareness/__init__.py`**

```python
"""Aleph Framework — Self-Awareness context injection (Phase 14)."""

from __future__ import annotations

from core.awareness.reader import AwarenessState, build_awareness_state
from core.awareness.injector import should_inject, build_injection

__all__ = ["AwarenessState", "build_awareness_state", "should_inject", "build_injection"]
```

- [ ] **Step 5: Run awareness tests**

```bash
.venv/bin/pytest tests/framework/test_awareness.py -v
```
Expected: all passing

- [ ] **Step 6: Commit**

```bash
rtk git add core/awareness/ tests/framework/test_awareness.py
git commit -m "feat(awareness): reader + injector with relevance gates"
```

---

### Task 8: Wire self-awareness into pipeline

**Files:**
- Modify: `core/engine/pipeline.py`

- [ ] **Step 1: Add awareness injection step to pipeline.py**

After the knowledge search block and before `run_agent()`, add:

```python
    # Self-awareness injection (DEFAULT OFF)
    if config.self_awareness.enabled and phone and redis_session and memory_ctx:
        try:
            from core.awareness.reader import build_awareness_state
            from core.awareness.injector import should_inject, build_injection

            flow_state = await redis_session.get_flow_state(phone) if flow_engine else None
            escalation = await redis_session.get_escalation(phone)

            awareness_state = await build_awareness_state(
                config=config,
                memory_ctx=memory_ctx,
                flow_state=flow_state,
                escalation=escalation,
            )

            if should_inject(config.self_awareness, awareness_state):
                awareness_text = build_injection(config.self_awareness, awareness_state)
                # Append to extra_instructions that will be passed to run_agent
                extra_awareness = f"\n\n{awareness_text}"
                logger.info(
                    "Self-awareness injection fired for %s (gap=%.0fmin)",
                    phone, awareness_state.elapsed_minutes,
                )
            else:
                extra_awareness = ""
        except Exception as e:
            logger.warning("Self-awareness injection failed (non-fatal): %s", e)
            extra_awareness = ""
    else:
        extra_awareness = ""
```

Pass `extra_awareness` to `run_agent()`:

```python
    result = await run_agent(
        registry=registry,
        user_message=user_message,
        message_history=message_history,
        memory_ctx=memory_ctx,
        extra_awareness=extra_awareness,     # ← NEW
    )
```

Update `run_agent` signature:

```python
async def run_agent(
    registry: AgentRegistry,
    user_message: str,
    message_history: list[dict] | None = None,
    memory_ctx=None,
    extra_awareness: str = "",               # ← NEW
) -> AgentResult:
```

In `run_agent`, merge into `extra_instructions`:

```python
    extra_instructions = ""
    if memory_ctx is not None and memory_ctx.summary:
        extra_instructions += f"\n\n[Contexto de conversas anteriores]\n{memory_ctx.summary}"
    if extra_awareness:
        extra_instructions += extra_awareness
```

- [ ] **Step 2: Run full test suite**

```bash
.venv/bin/pytest tests/ -q
```
Expected: 65 passed, 2 pre-existing failures

- [ ] **Step 3: Lint**

```bash
ruff check core/ tests/
```

- [ ] **Step 4: Final commit**

```bash
rtk git add core/engine/pipeline.py core/engine/runner.py
git commit -m "feat(awareness): wire self-awareness injection into pipeline (DEFAULT OFF)"
```

---

### Task 9: Update example config + docs

**Files:**
- Modify: `clients/example/config.yaml`
- Modify: `CLAUDE.md`
- Modify: `README.md`

- [ ] **Step 1: Add commented examples to clients/example/config.yaml**

After the existing `# --- Queue` section, add:

```yaml
# --- Self-Awareness (DEFAULT OFF) — inject prior state context on user return --------
# self_awareness:
#   enabled: true
#   return_gap_minutes: 30        # fire only when user returns after 30min+ gap
#   max_injection_age_hours: 4    # ignore states older than 4h
#   include_flow: true            # inject interrupted flow context
#   include_escalation: true      # inject escalation state if active
#   include_summary: true         # inject episodic memory summary
```

- [ ] **Step 2: Update CLAUDE.md**

In the "Completed Phases" section, add Phase 12, 13, 14.
In the Key Modules table, update `bifrost.py` → `llm_router.py`, add `core/session/memory.py` and `core/awareness/`.
In the YAML Config Structure table, add `self_awareness` row.

- [ ] **Step 3: Update README.md**

Add `self_awareness:` to YAML reference section:

```yaml
self_awareness:                    # DEFAULT OFF — inject prior state context on return
  enabled: false
  return_gap_minutes: 30           # min inactivity gap before injection fires
  max_injection_age_hours: 4       # ignore states older than this
  include_flow: true               # inject interrupted flow state
  include_escalation: true         # inject active escalation state
  include_summary: true            # inject episodic memory summary
```

Add `core/awareness/` to project structure tree.

- [ ] **Step 4: Final verification**

```bash
.venv/bin/pytest tests/ -q
ruff check core/ tests/
grep -rL "from __future__ import annotations" core/ tests/ --include="*.py"
```
Expected: tests pass, no lint errors, no files missing future import

- [ ] **Step 5: Final commit**

```bash
rtk git add clients/example/config.yaml CLAUDE.md README.md
git commit -m "docs: update example config and docs for phases 12-14"
```

---

## Verification Checklist

```bash
# 1. No files missing future import
grep -rL "from __future__ import annotations" core/ tests/ --include="*.py"
# Expected: empty output

# 2. No bifrost references remaining
grep -r "bifrost" core/ --include="*.py"
# Expected: empty output

# 3. Full test suite
.venv/bin/pytest tests/ -q
# Expected: ≥65 passed, 2 pre-existing failures unchanged

# 4. Lint
ruff check core/ tests/
# Expected: no errors

# 5. Schema smoke test
PYTHONPATH=. python -c "
from core.registry.schema import FrameworkConfig
c = FrameworkConfig(client_id='test', agent={'name':'Bot','model':'gpt-4o-mini'})
print('max_raw_turns:', c.sdk.sessions.max_raw_turns)
print('self_awareness:', c.self_awareness.enabled)
print('OK')
"
# Expected: max_raw_turns: 8, self_awareness: False, OK
```
