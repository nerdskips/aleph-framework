---
name: aleph-session
description: Use when configuring or debugging Redis session management in the Aleph Framework. Covers message buffer, anti-spam dedup, processing lock, conversation history, TTLs, and the sdk.sessions config section.
---

# Aleph Session

## What it is

All per-conversation state lives in Redis. The session layer handles four independent concerns — each with its own key namespace and TTL:

| Concern | Redis key | Purpose |
|---|---|---|
| Message buffer | `aleph:{cid}:buffer:{phone}` | Consolidate rapid messages before LLM |
| Anti-spam dedup | `aleph:{cid}:dedup:{phone}:{hash}` | Drop duplicate messages |
| Processing lock | `aleph:{cid}:lock:{phone}` | Prevent race conditions |
| Conversation history | `aleph:{cid}:history:{phone}` | LLM message history |

---

## Enable

Redis is required for all session features. Set `REDIS_URL` in `.env`:

```env
REDIS_URL=redis://localhost:6379/0
# or with auth:
REDIS_URL=redis://:password@host:6379/0
```

Without Redis, the bot still works — but buffer, dedup, lock, and history are all disabled. Flows and habits also require Redis.

---

## Full schema

```yaml
sdk:
  sessions:
    buffer_seconds: 8               # wait before processing (default: 8)
    dedup_window_seconds: 30        # ignore duplicate messages within this window (default: 30)
    lock_ttl_seconds: 60            # max time a phone can be locked (default: 60)
    history_ttl_seconds: 86400      # conversation history lifetime (default: 24h)
    max_history_messages: 20        # max messages kept in history (default: 20)
```

---

## Message buffer

Buffers rapid consecutive messages from the same phone into a single pipeline run.

**Example:** User types 3 messages in 5 seconds:
```
"oi"
"quero saber sobre"
"o produto X"
```

With `buffer_seconds: 8`, all three are concatenated and sent to the LLM as one message: `"oi quero saber sobre o produto X"`.

Tuning:
- Increase for users who type in bursts (mobile, voice-to-text)
- Decrease for high-volume bots where latency matters
- `buffer_seconds: 0` disables buffering entirely

---

## Anti-spam dedup

Prevents the same message from being processed twice. Common cause: Z-API webhook retries when your server is slow.

A hash of `(phone, message_content)` is stored in Redis with TTL = `dedup_window_seconds`. If the same hash arrives again within the window, it's silently dropped.

---

## Processing lock

Prevents race conditions when two messages from the same phone arrive simultaneously (e.g. buffered message + incoming while processing).

The lock is acquired before the pipeline starts and released after the response is sent. If the lock can't be acquired within a short timeout, the message is queued (not dropped).

`lock_ttl_seconds` is a safety valve — if a pipeline run crashes without releasing the lock, it expires automatically after this time.

---

## Conversation history

LLM message history is stored in Redis as a JSON list and injected at the start of each LLM call. This gives the agent memory within a session.

```yaml
sdk:
  sessions:
    history_ttl_seconds: 86400     # 24h — history expires after this
    max_history_messages: 20       # keep last 20 messages (10 turns)
```

`max_history_messages` is enforced by trimming the oldest messages when the list exceeds the limit. Balance between context quality and token cost.

---

## Redis key prefix

All keys are prefixed `aleph:{client_id}:` — multiple agents can share the same Redis instance without key collisions.

---

## Checking session state

Via Redis CLI:
```bash
# See all keys for an agent
redis-cli KEYS "aleph:my-agent:*"

# Check active buffer for a phone
redis-cli GET "aleph:my-agent:buffer:5511999999999"

# Check conversation history
redis-cli GET "aleph:my-agent:history:5511999999999"

# Check active lock
redis-cli TTL "aleph:my-agent:lock:5511999999999"
```

---

## Common mistakes

| Mistake | Fix |
|---|---|
| `REDIS_URL` not set in `.env` | Session features silently disabled — check startup logs |
| `buffer_seconds` too high | Users wait too long for a response — keep it ≤ 10s for most cases |
| `max_history_messages` too high | Each message costs tokens — 20 is the practical max for most models |
| `lock_ttl_seconds` too low | Long LLM calls (tools, knowledge search) get unlocked before finishing — use at least 60s |
| Multiple agents sharing Redis with same `client_id` | Keys collide — `client_id` in `config.yaml` must be unique per agent |
