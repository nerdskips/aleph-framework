---
name: aleph-messaging
description: Use when configuring or debugging WhatsApp messaging in the Aleph Framework. Covers Z-API webhook filter (groups, broadcasts, reactions), humanized sending, typing simulation, message splitting, and the api config section.
---

# Aleph Messaging

## What it is

Two modules handle all WhatsApp I/O:

- **`zapi_filter.py`** — parses incoming Z-API webhooks, filters noise (groups, broadcasts, reactions, human replies), routes to the pipeline
- **`zapi_send.py`** — sends responses with humanized delay, typing simulation, and automatic message splitting

---

## Webhook config

```yaml
api:
  port: 8000              # default
  webhook_path: /webhook  # default — Z-API must be pointed here
```

Register this URL in Z-API:
```
http://<your-server>:<port>/webhook
```

---

## What gets filtered (always, not configurable)

The filter silently drops:

| Message type | Why |
|---|---|
| Group messages | Bot only handles 1:1 |
| Broadcast messages | Not real conversations |
| Reaction messages (👍, ❤️, etc.) | Not text input |
| Bot's own messages | Prevents feedback loops |
| Status updates | Not conversations |

These never reach the pipeline.

---

## Human reply detection

If a human operator sends a message **from the responsible phone** to the same conversation, the bot detects it and pauses to avoid talking over the human. Configure via `human.responsible_phones`.

---

## Anti-spam (Redis dedup)

Duplicate messages (same content, same phone, within the dedup window) are silently dropped.

```yaml
sdk:
  sessions:
    dedup_window_seconds: 30   # default — messages arriving within 30s are deduped
```

Redis key: `aleph:{client_id}:dedup:{phone}:{message_hash}`

---

## Message buffer

Rapid consecutive messages from the same user are buffered and consolidated before hitting the pipeline. This prevents the LLM from answering each message fragment separately.

```yaml
sdk:
  sessions:
    buffer_seconds: 8    # wait 8s for more messages before processing (default)
```

Redis key: `aleph:{client_id}:buffer:{phone}`

---

## Humanized sending

The send module does **not** send responses instantly. It simulates human behavior:

1. Sends a typing indicator (`typing on`)
2. Waits proportionally to message length (configurable)
3. Sends the message
4. Turns typing off

Long responses are automatically split into multiple messages at sentence boundaries.

```yaml
agent:
  humanize_delay: true       # default: true
  max_message_length: 1000   # chars before splitting (default: 1000)
  typing_speed_cpm: 300      # chars per minute for delay calculation (default: 300)
```

---

## Message splitting

When a response exceeds `max_message_length`, it splits at the nearest sentence boundary (`.`, `!`, `?`) before the limit. Each chunk is sent as a separate message with a short delay between them.

To disable splitting (send one long message):
```yaml
agent:
  max_message_length: 0    # 0 = no splitting
```

---

## Z-API configuration

Z-API credentials live in `.env`, not in `config.yaml`:

```env
ZAPI_INSTANCE_ID=your-instance-id
ZAPI_TOKEN=your-token
ZAPI_CLIENT_TOKEN=your-client-token
ZAPI_BASE_URL=https://api.z-api.io
```

The framework reads these at runtime — never hardcode them in config.

---

## Dry run (no sending)

For testing without actually sending WhatsApp messages:

```yaml
debug:
  dry_run: true   # log what would be sent, don't actually send
```

Responses are logged at INFO level with prefix `[DRY RUN]`.

---

## Common mistakes

| Mistake | Fix |
|---|---|
| Webhook URL has trailing slash | Z-API is strict — use exact path `/webhook` |
| Group messages reaching the pipeline | Not configurable — groups are always filtered. Check that Z-API instance is personal, not business group |
| Bot replies to its own messages | Check that `ZAPI_INSTANCE_ID` matches the sending instance — filter uses this to detect own messages |
| `buffer_seconds: 0` | Pipeline runs on every message fragment — LLM gets half-sentences |
| Typing delay too long | Lower `typing_speed_cpm` or disable `humanize_delay` for high-volume bots |
