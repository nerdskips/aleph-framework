---
name: aleph-human
description: Use when configuring or debugging human escalation (HITL) in the Aleph Framework. Covers responsible_phones, takeover mode, escalation flow, session TTL, Redis escalation state, and how the bot resumes after handoff.
---

# Aleph Human (HITL)

## What it is

Human-in-the-loop escalation. When triggered, the bot pauses, notifies a responsible person via WhatsApp, and hands the conversation to them. The bot resumes automatically after the session expires or when the human releases control.

---

## Enable

```yaml
human:
  enabled: true
  responsible_phones: ["5511999999999"]   # who gets notified on escalation
  escalation_session_ttl: 1800            # seconds before bot auto-resumes (default: 30min)
  notification_message: "Novo atendimento aguardando: {phone}"  # optional
```

---

## Full schema

```yaml
human:
  enabled: true
  responsible_phones:
    - "5511999999999"    # must include country code, no + or spaces
    - "5521888888888"    # multiple responsibles — all are notified
  escalation_session_ttl: 1800          # seconds (default: 1800)
  notification_message: "string"        # optional custom notification
  pause_message: "string"               # sent to user when bot pauses (optional)
  resume_message: "string"              # sent to user when bot resumes (optional)
```

---

## Triggering escalation

Three ways to escalate:

### 1. Guardrail pattern

```yaml
guardrails:
  input_patterns:
    - name: falar-com-humano
      keywords: ["falar com atendente", "quero atendente", "humano"]
      action: escalate          # checks habits first, escalates if no match
      priority: 20

    - name: emergencia
      keywords: ["urgente", "emergência"]
      action: escalate_no_habit   # always escalates, skips habits
      priority: 50
```

### 2. Takeover (immediate, no LLM)

```yaml
    - name: vip-client
      keywords: ["vip", "executivo"]
      action: takeover   # bot pauses immediately, human takes full control
      priority: 30
```

### 3. Flow on_complete

```yaml
flows:
  flows:
    - id: reclamacao
      on_complete:
        action: escalate
        escalate_phone: "5511999999999"   # specific responsible for this flow
```

---

## escalate vs escalate_no_habit vs takeover

| Action | Habits checked? | LLM called? | Use for |
|---|---|---|---|
| `escalate` | Yes | If habit matches | General "I want to talk to someone" |
| `escalate_no_habit` | No | No | Always escalate immediately |
| `takeover` | No | No | Full control transfer, no bot |

---

## Escalation flow

```
User: "quero falar com atendente"
  → guardrail matches (action: escalate)
  → habits search — if match found, LLM answers from habit (no escalation)
  → no habit match → escalation starts:
      1. User receives pause_message ("Um atendente será notificado em breve...")
      2. Responsible phones receive notification via Z-API
      3. Bot stops responding to this phone number
      4. Human attends via their own WhatsApp
      5. TTL expires OR human releases → bot resumes, user receives resume_message
```

---

## Redis escalation state

```
aleph:{client_id}:escalation:{phone}  →  {
  "escalated_at": 1712345678,
  "responsible": "5511999999999",
  "ttl": 1800
}
```

TTL is set on the Redis key — when it expires, the bot auto-resumes. The human can also release early by sending a specific command (if configured).

---

## Checking active escalations

Via Postgres MCP or Redis:
```
# All phones currently in escalation for a given agent
KEYS aleph:my-agent:escalation:*
```

---

## Multiple responsibles

When `responsible_phones` has multiple entries, **all** receive the notification. The first human to pick up handles the conversation — the bot tracks by phone, not by responsible.

---

## Common mistakes

| Mistake | Fix |
|---|---|
| Phone number with `+` or spaces | Must be `5511999999999` format — no `+`, no spaces |
| `human.enabled: false` but using `escalate` action | Escalation silently becomes a `block` — always enable when using escalate actions |
| `escalation_session_ttl` too short | Bot resumes while human is still typing — use at least 1800s (30min) |
| No `pause_message` | User gets no feedback that a human was notified — always set one |
| Habits returning a match for every escalation trigger | Lower `habits.search_threshold` or make the escalation pattern more specific |
