---
name: aleph-guardrails
description: Use when configuring guardrail patterns for an Aleph Framework agent — input patterns (pre-LLM keyword/regex rules) or output rules (post-LLM fabrication/price/ghost checks). Covers all 9 guardrail actions, pattern priority, inject/redirect messages, and output rule types.
---

# Aleph Guardrails

## Overview

Two complementary layers run automatically:

- **Input guardrails** — deterministic, run **before** the LLM. Zero cost, regex/keyword only.
- **Output guardrails** — deterministic, run **after** the LLM response. Three default guards always on.

The SDK also runs its own guardrails **in parallel** with the LLM (tripwire system) — that is separate and already enabled by default.

---

## Input Patterns

Each pattern in `guardrails.input_patterns[]` is evaluated in priority order (highest first). First match wins.

```yaml
guardrails:
  input_patterns:
    - name: "rule-name"          # identifier for logs
      keywords: ["word", "phrase"]   # lowercased, accent-normalized matches
      regex: ["pattern.*"]           # applied after normalization
      action: redirect               # see actions table below
      priority: 10                   # higher = evaluated first (default: 0)
      redirect_message: "Text"       # required when action=redirect
      inject_instruction: "Text"     # required when action=inject
      tool_choice: "auto"            # "auto" or "required"
```

### The 9 Actions

| Action | LLM called? | Use for |
|---|---|---|
| `continue` | Yes | Explicitly pass to LLM (rarely needed — it's the default) |
| `redirect` | No | Canned reply, skip LLM entirely. Requires `redirect_message`. |
| `block` | No | Safe refusal, no escalation. For off-topic or abuse. |
| `inject` | Yes | Add extra instruction to the LLM input. Requires `inject_instruction`. |
| `escalate` | Depends | Check habits first; escalate to human if no match. |
| `escalate_no_habit` | No | Always escalate, never check habits. |
| `takeover` | No | Human assumes full control of the chat immediately. |
| `tool_required` | Yes | Force `tool_choice=required` — LLM must call a tool. |
| `bypass_llm` | No | Skip LLM entirely (pending full implementation). |

### Common Pattern Examples

```yaml
guardrails:
  input_patterns:
    # Fast greeting — skip LLM cost
    - name: greeting
      keywords: ["oi", "olá", "boa tarde", "bom dia", "boa noite"]
      action: redirect
      redirect_message: "Olá! Como posso te ajudar hoje?"
      priority: 5

    # Human escalation
    - name: falar-com-humano
      keywords: ["falar com atendente", "quero falar com alguém", "atendimento humano"]
      regex: ["fal(a|ar) com (humano|pessoa|atendente)"]
      action: escalate
      priority: 20

    # Price inquiry — inject context before LLM
    - name: preco-inject
      keywords: ["quanto custa", "qual o preço", "valor"]
      action: inject
      inject_instruction: "O usuário perguntou sobre preço. Consulte a tabela de preços antes de responder. Nunca invente valores."
      priority: 15

    # Abuse / off-topic block
    - name: abuso
      regex: ["palavr[aã]o", "xingamento"]
      action: block
      priority: 100
```

---

## Output Rules

Three guards are **DEFAULT ON** for every agent. They run after every LLM response:

| Guard | What it catches |
|---|---|
| `enable_fabrication_guard` | Invented addresses, fake branches, internal data |
| `enable_price_leak_guard` | Prices mentioned outside a budget/quote context |
| `enable_ghost_escalation_guard` | LLM says "vou escalonar" but didn't call the tool |

To add custom output rules:

```yaml
guardrails:
  output_rules:
    - name: "no-competitor-mention"
      type: custom_regex
      patterns: ["concorrente X", "empresa Y"]
      safe_response: "Desculpe, não posso comentar sobre isso. Posso te ajudar com outra coisa?"
      enabled: true
```

### Output Rule Types

| Type | Use for |
|---|---|
| `fabrication` | Block invented factual claims |
| `price_leak` | Block loose prices outside budget context |
| `ghost_escalation` | Block LLM claiming to escalate without calling tool |
| `custom_regex` | Any pattern-based output filter |

Use `exempt_intents` to skip a rule when the input matched a specific guardrail pattern:

```yaml
    - name: price-guard
      type: price_leak
      exempt_intents: ["orcamento", "preco-inject"]   # these input patterns bypass this output rule
```

---

## Normalization

Always on. Text is lowercased and accent-stripped before matching. Your keywords should be lowercase and unaccented:

```yaml
keywords: ["nao quero", "cancelar"]  # matches "Não quero" and "CANCELAR"
```

---

## Common Mistakes

| Mistake | Fix |
|---|---|
| `redirect` with no `redirect_message` | Always set `redirect_message` when action=redirect |
| `inject` with no `inject_instruction` | Always set `inject_instruction` when action=inject |
| Regex not compiling | Test with `python -c "import re; re.compile('your_pattern')"` |
| All rules same priority | Use priority to control evaluation order explicitly |
| Blocking everything with `block` | Use `redirect` for friendly refusals, `block` for safety only |
