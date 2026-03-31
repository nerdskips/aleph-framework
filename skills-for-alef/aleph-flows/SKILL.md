---
name: aleph-flows
description: Use when building or debugging multi-step conversation flows in the Aleph Framework. Covers FlowConfig schema, step definitions, transitions, interruption handling, Redis state, and how flows integrate with the pipeline.
---

# Aleph Flows

## What it is

A declarative state machine where the **framework** controls conversation progression and the LLM only handles per-step dialogue. Each step has an instruction, the user responds, the framework validates and moves to the next step.

Redis key: `aleph:{client_id}:flow:{phone}`

---

## Enable

```yaml
flows:
  enabled: true
  flows:
    - id: agendamento
      trigger_patterns: ["quero agendar", "marcar consulta"]
      steps: [...]
      on_complete:
        action: message
        message: "Agendamento confirmado! Até logo."
```

`enabled` defaults to `false` — explicitly set to `true`.

---

## Full schema

```yaml
flows:
  enabled: true
  flows:
    - id: string                          # unique identifier
      name: string                        # display name (optional)
      trigger_patterns: [string]          # keywords/phrases that start this flow
      steps:
        - id: string                      # unique within the flow
          instruction: string             # injected into LLM prompt for this step
          validation_pattern: string      # regex — if set, input must match to advance
          validation_error: string        # message shown when validation fails
          on_interruption: cancel | pause # what to do if user goes off-topic (default: cancel)
      on_complete:
        action: message | tool | escalate
        message: string                   # when action=message
        tool_name: string                 # when action=tool
        escalate_phone: string            # when action=escalate
```

---

## Step-by-step example

```yaml
flows:
  enabled: true
  flows:
    - id: pedido
      trigger_patterns: ["fazer pedido", "quero pedir", "pedido"]
      steps:
        - id: nome
          instruction: "Pergunte o nome completo do cliente para o pedido."
          validation_pattern: "[A-Za-zÀ-ú ]{3,}"
          validation_error: "Por favor, informe seu nome completo."

        - id: produto
          instruction: "Pergunte qual produto o cliente deseja e a quantidade."

        - id: endereco
          instruction: "Pergunte o endereço completo para entrega."
          on_interruption: pause

        - id: confirmacao
          instruction: "Resuma o pedido (nome, produto, endereço) e peça confirmação."
          validation_pattern: "sim|confirmo|ok|yes"
          validation_error: "Por favor, confirme com 'sim' ou corrija as informações."

      on_complete:
        action: message
        message: "Pedido registrado! Em breve entraremos em contato."
```

---

## on_interruption

Controls what happens when the user sends something unrelated to the current step:

| Value | Behavior |
|---|---|
| `cancel` | Flow is cancelled, user returns to normal conversation (default) |
| `pause` | Flow is paused, resumes when user re-engages with trigger |

Use `pause` for steps where the user might need to go check something (e.g. get their address).

---

## on_complete actions

| Action | Use for |
|---|---|
| `message` | Send a final message and close the flow |
| `tool` | Call a registered tool with collected data |
| `escalate` | Hand off to a human with the full collected context |

---

## How it connects to the pipeline

```
Input → Guardrails → Flow resolution → Knowledge search → LLM → Output guardrails
                         ↑
                   FlowEngine checks Redis for active flow state
                   If active: injects step instruction, validates response, advances state
                   If trigger matched: starts new flow
```

`FlowEngine` is only initialized when `flows.enabled: true` AND Redis is connected. Without Redis, flows are silently disabled.

---

## Redis state structure

```
aleph:{client_id}:flow:{phone}  →  {
  "flow_id": "pedido",
  "current_step": "endereco",
  "collected": {
    "nome": "João Silva",
    "produto": "2x Hambúrguer"
  },
  "started_at": 1712345678
}
```

State expires after `flow_session_ttl` seconds (default: 1800 — 30 minutes).

---

## Testing flows

```bash
aleph-agent chat <name>
# → type the trigger phrase
# → walk through each step
# → verify validation_error fires on bad input
# → confirm on_complete message appears
```

Via MCP:
```
chat_message("<name>", "quero fazer um pedido")
```

---

## Common mistakes

| Mistake | Fix |
|---|---|
| `enabled: true` but no Redis | Flows silently disabled — set `REDIS_URL` in `.env` |
| trigger_patterns too generic | "oi" would start a flow on every greeting — be specific |
| No `validation_pattern` on confirmation step | LLM confirms even on "não" — always validate the confirmation step |
| Forgetting `on_interruption: pause` on address/document steps | Users go check info and the flow cancels on them |
