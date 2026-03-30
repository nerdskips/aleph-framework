---
name: aleph-yaml
description: Create or modify Aleph Framework agent config.yaml files. Use when building a new agent configuration, adding guardrails, configuring knowledge base, setting up tools, or troubleshooting YAML validation errors. Triggers on config.yaml creation, YAML errors, or agent setup tasks.
---

# Aleph YAML — Agent Configuration Patterns

Agent config.yaml is the single source of truth for an agent's behavior. The framework reads it on startup and validates against Pydantic schema.

## Minimal Config (agent with no tools)

```yaml
client_id: "meu-agente"

agent:
  name: "Meu Agente"
  model: "gpt-4.1-mini"

human:
  enabled: false
```

## Complete Config Template

```yaml
client_id: "nome-do-agente"

agent:
  name: "Nome Visível do Agente"
  model: "openai/gpt-4.1-mini"       # via Bifrost (com prefixo)
  # model: "gpt-4.1-mini"            # via API key direta (sem prefixo)
  fallback_model: "google/gemini-2.5-flash"
  temperature: 0.4
  max_tokens: 1024

api:
  port: 8000

human:
  enabled: true
  responsible_phones: ["5534999999999"]
  escalation_session_ttl: 7200
  hold_message: "Vou verificar com a equipe e já te retorno!"

guardrails:
  enable_fabrication_guard: true
  enable_price_leak_guard: true        # false se agente precisa falar preços
  enable_ghost_escalation_guard: true
  input_patterns:
    - name: "cancelamento"
      keywords: ["cancelar", "quero cancelar"]
      action: "escalate"
      priority: 20
    - name: "fora_de_escopo"
      keywords: ["futebol", "previsao do tempo"]
      action: "redirect"
      redirect_message: "Só consigo ajudar com assuntos do negócio!"
      priority: 10
  output_rules:
    - name: "custom_check"
      type: "custom_regex"
      patterns: ["padrao_regex_aqui"]
      safe_response: "Mensagem segura"

knowledge:
  enabled: true
  auto_migrate: true
  schema: "knowledge"
  embedding_dimensions: 1536
  auto_search: true
  auto_search_top_k: 5
  tool_search: true
  chunk_size: 500
  chunk_overlap: 75

habits:
  enabled: true
  auto_migrate: true
  search_before_escalate: true

messaging:
  disclaimer:
    enabled: true
    text: "Agente de atendimento"
  send_as_paragraphs: true

tools:
  - name: "consultar_pedido"
    type: "webhook"
    description: "Consulta status do pedido"
    webhook_url: "https://n8n.example.com/webhook/pedidos"
    parameters:
      numero:
        type: "string"
        description: "Número do pedido"
    returns: "JSON com status"
```

## Guardrail Actions (9 available)

| Action | Behavior | Uses LLM? |
|---|---|---|
| `continue` | Pass to LLM | Yes |
| `redirect` | Reply with redirect_message, skip LLM | No |
| `block` | Reply with safe message, skip LLM | No |
| `inject` | Add instruction to LLM input | Yes |
| `escalate` | Check habits first, escalate if no match | Depends |
| `escalate_no_habit` | Always escalate | No |
| `takeover` | Human takes over chat | No |
| `tool_required` | Force tool_choice on LLM | Yes |
| `bypass_llm` | Skip LLM entirely (future) | No |

## Tool params_in Modes

| Mode | Behavior |
|---|---|
| `auto` | URL templates resolved, rest in body (POST) or query (GET) |
| `body` | Everything in JSON body |
| `query` | Everything as ?key=val |
| `url` | Everything resolved in URL via {param} |

## Common YAML Mistakes

1. Indentation must be spaces, never tabs
2. List items (`-`) must align with parent field
3. `name`, `keywords`, `action` in input_patterns must be at same indent level
4. `output_rules` require `type` field (fabrication, price_leak, ghost_escalation, custom_regex)
5. Boolean values: `true`/`false` (not `True`/`False`)
6. Strings with special chars need quotes: `"R$0,75"`