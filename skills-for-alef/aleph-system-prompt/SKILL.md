---
name: aleph-system-prompt
description: Use when writing or editing the system prompt for an Aleph Framework agent. Covers structure, persona definition, instruction clarity, tone for BR Portuguese, length guidelines, and what to never put in a system prompt.
---

# Aleph System Prompt

## Where it lives

```
clients/<name>/prompts/system.md
```

Configured in `config.yaml`:

```yaml
agent:
  system_prompt_file: prompts/system.md   # default, can be overridden
```

The file is read at boot time and injected verbatim as the `system` message before every conversation.

---

## Recommended structure

```markdown
# <Agent Name>

## Identity
Who the agent is, what business it represents, what its job is.
One short paragraph. First person ("Você é...").

## Responsibilities
Bulleted list of what the agent CAN and SHOULD do.

## Restrictions
Bulleted list of what the agent must NEVER do.
Be explicit — LLMs fill gaps with hallucinations.

## Tone
Formal / informal / neutral. Language (PT-BR, EN, ES).
Emoji usage policy.
Response length target.

## Context (optional)
Static facts injected here — business hours, address, pricing tiers.
Only put facts that never change. For dynamic data use data_files in config.yaml.
```

---

## PT-BR specifics

- Write the prompt in the same language the agent will use — if the agent talks in PT-BR, write the prompt in PT-BR
- Use "Você" not "Tu" for formal Brazilian register
- Spell out accents normally — the framework normalizes input for guardrails but the LLM receives raw text
- Avoid idioms that are region-specific (SP vs NE) unless the brand calls for it

---

## Length guidelines

| Agent type | Target length |
|---|---|
| Simple FAQ / redirect-heavy | 200–400 words |
| Sales / product info | 400–800 words |
| Complex multi-step (flows) | 600–1200 words |

Longer is not better. Each extra sentence is a chance for the LLM to get confused. Be precise.

---

## What NOT to put in the system prompt

| Avoid | Why | Use instead |
|---|---|---|
| Prices that change | LLM will confidently state stale prices | `data_files` with `price_inject` guardrail |
| Long product catalogs | Wastes context, hurts coherence | `knowledge` RAG |
| Escalation logic | Fragile — LLM may or may not follow it | `guardrails.input_patterns` with `escalate` action |
| Secrets / credentials | Prompt can be extracted | `.env` only |
| "Never say X" for safety | LLMs ignore negations under pressure | Use `guardrails.output_rules` with `custom_regex` |

---

## data_files injection

For facts that belong in the prompt but live in a file:

```yaml
data_files:
  - key: cardapio
    file: data/cardapio.json
  - key: horarios
    file: data/horarios.txt
```

Then reference the key in the prompt:

```markdown
## Menu
{{cardapio}}
```

The framework substitutes at boot time. Keep these files small — they count against context.

---

## Prompt for flows

When `flows.enabled: true`, add a section describing flow behavior:

```markdown
## Atendimento estruturado
Quando iniciar um processo (ex: pedido, agendamento), siga exatamente os passos
solicitados. Não pule etapas. Não invente informações que não foram coletadas ainda.
Aguarde a confirmação do usuário em cada etapa antes de avançar.
```

---

## Quick validation

After editing:

```bash
aleph-agent test <name>   # checks system_prompt_file exists and has >20 chars
```

Or via MCP:

```
validate_agent("<name>")
```
