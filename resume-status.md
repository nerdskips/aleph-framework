# Aleph Framework — Resumo do Projeto (Atualizado)

> Atualizado em 27/03/2026 — Sessões de desenvolvimento com Claude Opus 4.6
> Repo: `https://github.com/nerdskips/aleph-framework`
> Server: Hostinger KVM2 `/root/zuper-framework/`
> Status: **Fase 8 pendente** — Flows (State Machine) é o próximo

---

## Objetivo

Framework config-driven proprietário onde cada agente WhatsApp é uma pasta com YAML + tools, sem tocar no core. Um dev junior sobe um agente novo em 1-2 dias.

Baseado nos 14 pilares extraídos do agente Laura (Le Crocant) em produção.

**Distribuição:** pacote pip instalável via repo Git privado + CLI (`aleph-agent init/start/test/chat`).

---

## Como Estamos Desenvolvendo

- Claude Opus 4.6 assiste no design de arquitetura, implementação de código e documentação
- Álef implementa no server, testa end-to-end, e valida decisões de produto
- Cada fase gera: código funcional + documentação de desenvolvimento (.md)
- Testes são feitos com agentes reais via WhatsApp/Z-API e CLI interativo
- O agente `teste-hitl` validou escalation end-to-end com sucesso
- O agente `padaria` validou knowledge base RAG end-to-end com sucesso

---

## Fases Completadas

| Fase | Entregável | Status |
|---|---|---|
| 1 | Core framework (pipeline, guardrails, tools, messaging, session, LLM) | ✅ Completo |
| 2 | Human-in-the-loop — escalation (escalonar → notificar → quote/reply → reformular → responder) | ✅ Completo e testado E2E |
| 3 | Hábitos operacionais — hybrid search RRF, classificação LLM (GERAL/ÚNICO), dedup, Postgres genérico | ✅ Código pronto + wiring no pipeline |
| 4 | Docker — Dockerfile + compose multi-agent (cada agente = container + .env próprio) | ✅ Completo |
| 4.5 | API key direta — suporte a OpenAI/Gemini/DeepSeek/OpenRouter sem Bifrost, auto-detecção | ✅ Completo |
| 5 | Pacote pip + CLI — `aleph-agent init/start/stop/test/list/chat/knowledge` | ✅ Completo |
| 5.1 | Rebranding Zuper → Aleph + distribuição via Git privado + licença proprietária | ✅ Completo |
| 6 | Timezone — `TZ` no .env, injeção de data/hora no prompt antes de cada call LLM | ✅ Completo |
| 7 | Knowledge Base (RAG) — hybrid search RRF, contextual retrieval, CLI de ingestão, auto_search pré-LLM | ✅ Completo e testado E2E |

---

## Próximos Passos (por prioridade)

### Fase 8 — Flows (State Machine) ← PRÓXIMO

**Status: Arquitetura desenhada, implementação pendente.**

Framework controla o fluxo, LLM só faz a parte conversacional de cada passo. Dev declara steps no YAML, framework gerencia state no Redis, alimenta um passo por vez pro LLM.

**Arquitetura definida:**

```yaml
flows:
  - name: "pedido_pizza"
    trigger_keywords: ["quero pedir", "fazer pedido"]
    steps:
      - field: "sabor"
        prompt: "Pergunte o sabor. Opções do cardápio."
        validation: "required"
      - field: "tamanho"
        prompt: "Pergunte o tamanho: P, M ou G"
        validation: "options:P,M,G"
      - field: "borda"
        prompt: "Pergunte borda recheada ou normal"
        validation: "options:recheada,normal"
      - field: "endereco"
        prompt: "Peça o endereço de entrega"
        validation: "required"
      - field: "pagamento"
        prompt: "Pergunte forma de pagamento"
        validation: "options:pix,cartao,dinheiro"
    on_complete:
      action: "webhook"
      webhook_url: "https://n8n.example.com/webhook/novo-pedido"
      confirmation: true
      success_message: "Pedido enviado!"
```

**Lógica do pipeline:**

```
mensagem chega
  → checa se tem flow ativo no Redis pra esse phone
  → se sim: injeta APENAS o passo atual pro LLM
    → LLM responde
    → framework extrai valor do campo via LLM call pequena
    → valida (required, options, regex)
    → se válido: salva no state, avança
    → se inválido: repete o passo
  → se não: checa trigger_keywords
    → se match: cria state, começa passo 1
    → se não: pipeline normal (knowledge, LLM livre, etc)
```

**on_complete — ações disponíveis:**

| action | O que faz |
|---|---|
| `webhook` | POST com todos os campos coletados como JSON pra URL configurada |
| `escalate` | Escalona pro humano com dados coletados |
| `confirm` | Confirma com cliente e limpa state |
| `tool` | Chama uma tool code Python do dev |

**Redis key:** `aleph:{cid}:flow:{phone}` — guarda flow ativo, step atual, dados coletados. TTL configurável.

**Suporte a múltiplos flows:** sim, lista no YAML. Um flow ativo por vez por phone.

**Arquivos a criar:**

```
core/flows/
  ├── __init__.py
  ├── schema.py       # Pydantic models (FlowConfig, StepConfig, OnCompleteConfig)
  ├── engine.py       # Lógica de fluxo (check step, extract, validate, advance)
  ├── state.py        # Redis state management (get/set/clear flow state)
  └── complete.py     # Ações de on_complete (webhook, escalate, confirm, tool)
```

**Patch necessário:** `pipeline.py` — novo step antes do LLM pra checar flow ativo.

**Informações necessárias antes de implementar:**
- Schema das tools webhook (`core/registry/schema.py`) — pra reaproveitar padrão de POST
- Bloco do FrameworkConfig onde tá habits e knowledge — pra adicionar flows
- Método `_key` do Redis session — pra adicionar keys de flow

---

### Fase 9 — MCP Server (Fábrica de Agentes)

**Status: Planejado, não iniciado.**

MCP server que expõe as funções do framework como tools pro Claude Code. Permite criar agentes inteiros via conversa.

**Tools planejadas:**

| Tool | O que faz |
|---|---|
| `init_agent(name, model, port)` | Cria scaffold do agente |
| `edit_config(name, yaml_content)` | Atualiza config.yaml |
| `edit_prompt(name, prompt_content)` | Atualiza system prompt |
| `add_guardrail(name, type, keywords, action)` | Adiciona guardrail no YAML |
| `add_flow(name, flow_config)` | Adiciona um flow |
| `knowledge_load(name, file_path)` | Ingere arquivo na base |
| `test_agent(name)` | Valida config |
| `chat(name, message)` | Testa agente interativamente |
| `start_agent(name)` | Builda e sobe container |
| `list_agents()` | Lista agentes |

**Caso de uso:** dev cola regras de negócio do cliente → Claude Code via MCP constrói o agente inteiro (YAML, prompt, guardrails, flows, knowledge) em 5 minutos.

**Implementação:** `core/mcp/server.py` usando SDK MCP oficial do Python. ~200 linhas, não mexe no core existente.

---

### Fase 10 — Documentação

**Status: Planejado, não iniciado.**

Dois documentos:

1. **User Guide** — pra humanos, formato clean, passo a passo:
   - Install → init → config → deploy
   - Guia de YAML com exemplos por segmento (padaria, clínica, loja)
   - Guia de tools (webhook + code)
   - Guia de flows
   - Guia de knowledge base
   - Troubleshooting

2. **Agent Builder Prompt** — .md denso pra Claude Code:
   - Arquitetura completa do framework
   - Schema YAML completo com todos os campos
   - Padrões de tools, guardrails, flows
   - Exemplos de agentes por segmento
   - Serve de contexto pro Claude Code criar agentes via MCP

---

## Arquitetura

```
config.yaml (client) → Loader (valida) → Registry (monta) → Pipeline (executa)
```

**3 camadas:**

1. **Client layer** (pasta do agente) — YAML + prompt + tools + data + .env. Específico por agente, nunca toca no core.
2. **Registry** (`core/registry/`) — ponte entre YAML e runtime. Lê config, valida com Pydantic, carrega tools dinamicamente, monta objetos tipados.
3. **Core** (`core/`) — genérico, nunca muda por cliente. Engine, guardrails, session, messaging, LLM, tools, API, human, habits, knowledge, flows.

**Pipeline de execução:**

```
Z-API webhook
  → Filtro (11+ tipos: grupo, newsletter, reaction, edit, pin, etc)
  → Human reply detection (quote do responsável = resposta de escalation)
  → Takeover detection (humano digitando no WhatsApp do agente)
  → Anti-spam (messageId dedup, TTL 120s)
  → Buffer (mensagens picadas, consolida em 8s)
  → Lock de processamento (por phone, evita duplicatas)
  → Guardrail INPUT (determinístico, regex/keywords, pré-LLM, zero custo)
    → Se redirect/block: responde direto, pula LLM
    → Se inject: adiciona instrução extra pro LLM
    → Se escalate: busca hábitos primeiro (se enabled)
      → Se hábito encontrado: injeta contexto, vai pro LLM (sem escalonar)
      → Se sem hábito: pausa, notifica humano, retorna hold message
    → Se escalate_no_habit: SEMPRE escalona, nunca consulta hábitos
  → Flow check (se flow ativo, injeta apenas passo atual)          ← NOVO (Fase 8)
  → Knowledge search (hybrid RRF, injeta contexto pré-LLM)
  → Agent SDK 0.12 (primary model via provider configurado)
    → Se falha: fallback model automático
  → Guardrail OUTPUT (pós-LLM)
    → Fabricação, price leak, ghost escalation
    → Isentado quando tools foram chamadas
  → Envio humanizado (quebra por parágrafo, delay aleatório, disclaimer)
```

---

## Stack

| Camada | Tecnologia | Status |
|---|---|---|
| Orquestração | OpenAI Agents SDK 0.12.5 | ✅ Integrado |
| LLM Gateway | Bifrost (Go, porta 8080) OU API key direta | ✅ Ambos suportados |
| LLM Providers | OpenAI, Gemini, DeepSeek, OpenRouter, custom | ✅ Auto-detecção |
| Guardrails input | Core próprio (regex/keywords YAML, 9 ações) | ✅ Implementado |
| Guardrails output | Core próprio (fabricação, price, ghost) + custom regex | ✅ Implementado |
| Sessão/Estado | Redis (CloudFy externo) | ✅ Implementado |
| WhatsApp | Z-API (filtro + envio humanizado) | ✅ Implementado |
| Tools webhook | Gerador dinâmico de YAML → @function_tool (4 modos params_in) | ✅ Implementado |
| Tools code | Import dinâmico de Python modules | ✅ Implementado |
| Human-in-the-loop | Escalation + quote/reply Z-API + LLM reformulation | ✅ Implementado e testado E2E |
| Hábitos | Hybrid search RRF + dedup + classificação LLM (Postgres genérico) | ✅ Código pronto, wired no pipeline |
| Knowledge Base | Hybrid search RRF + contextual retrieval + CLI ingestão | ✅ Implementado e testado E2E |
| Database | asyncpg + auto-bootstrap (pgvector, unaccent, tabelas, indexes, RPC) | ✅ Implementado |
| Docker | Dockerfile + compose multi-agent (.env por agente) | ✅ Implementado |
| API | FastAPI (webhooks) | ✅ Implementado |
| Debug/Tracing | SDK tracing + structured logging JSON | ✅ Básico ativo |
| CLI | aleph-agent init/start/stop/test/list/chat/knowledge | ✅ Implementado |
| Timezone | TZ env var + injeção de data/hora no prompt | ✅ Implementado |
| Distribuição | Git privado + pip install + licença proprietária | ✅ Implementado |
| CI/CD | GitHub Actions — push tag → build → publish | ✅ Configurado |
| Flows | State machine declarativa YAML + Redis state | ⏳ Arquitetura pronta, implementação pendente |
| MCP Server | Fábrica de agentes via Claude Code | ⏳ Planejado |
| SDK Guardrails | Tripwire system (paralelo ao LLM) | ⏳ Schema pronto, wiring pendente |
| SDK Sessions | Redis nativo (openai-agents[redis]) | ⏳ Schema pronto, wiring pendente |
| SDK Handoffs | Peer + Manager patterns | ⏳ Schema pronto, wiring pendente |
| Takeover completo | Transbordo (humano assume chat inteiro) | ⏳ Redis pronto, lógica de fluxo pendente |
| Follow-up | Timers de inatividade | ⏳ Não iniciado |
| Mídia | Whisper + Vision + pypdf | ⏳ Não iniciado |
| Fila | ARQ (Redis-based) | ⏳ Não iniciado |
| Eval/Testing | Promptfoo + pytest | ⏳ Não iniciado |

---

## Árvore de Arquivos (Atual)

```
aleph-framework/
├── .env                           # Segredos de infra (git-ignored)
├── .env.example
├── .gitignore
├── .dockerignore
├── LICENSE                        # Proprietary — Álef Souza
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml                 # Deps + setuptools + entry point aleph-agent
│
├── core/                          # GENÉRICO — nunca muda por cliente
│   ├── __init__.py
│   │
│   ├── registry/
│   │   ├── __init__.py
│   │   ├── schema.py              # Pydantic models (20+ classes, 3 tiers defaults)
│   │   ├── loader.py              # Lê YAML, valida, carrega prompt e data (suporta AGENT_DIR)
│   │   ├── tool_loader.py         # Import dinâmico (webhook + code)
│   │   └── registry.py            # AgentRegistry.from_config()
│   │
│   ├── engine/
│   │   ├── __init__.py
│   │   ├── runner.py              # Executa agente SDK + fallback + timezone injection
│   │   └── pipeline.py            # Guardrail → knowledge → habits → agent → output check
│   │
│   ├── guardrails/
│   │   ├── __init__.py
│   │   ├── input.py               # Engine de patterns YAML (keywords + regex, 9 ações)
│   │   └── output.py              # Fabricação + price leak + ghost escalation + custom
│   │
│   ├── tools/
│   │   ├── __init__.py
│   │   └── webhook.py             # YAML webhook → @function_tool (4 modos params_in)
│   │
│   ├── session/
│   │   ├── __init__.py
│   │   ├── redis.py               # Buffer, anti-spam, lock, context, takeover, LID, escalation
│   │   └── redis_escalation.py    # EscalationData dataclass
│   │
│   ├── messaging/
│   │   ├── __init__.py
│   │   ├── zapi_filter.py         # 11+ filtros Z-API + is_human_reply + is_human_takeover
│   │   └── zapi_send.py           # Envio humanizado + send_notification
│   │
│   ├── llm/
│   │   ├── __init__.py
│   │   └── bifrost.py             # Multi-provider (Bifrost/OpenAI/Gemini/DeepSeek/OpenRouter/custom)
│   │
│   ├── api/
│   │   ├── __init__.py
│   │   └── webhooks.py            # /webhook/zapi + /webhook/humano + /health + knowledge init
│   │
│   ├── human/
│   │   ├── __init__.py
│   │   └── escalation.py          # escalate_to_human + handle_human_response + store_habit
│   │
│   ├── habits/
│   │   ├── __init__.py
│   │   ├── database.py            # Postgres connection + auto-bootstrap
│   │   ├── embeddings.py          # Embedding via Bifrost/provider (independente)
│   │   ├── search.py              # Hybrid search RRF (tsvector + pgvector)
│   │   └── store.py               # Gravação com classificação LLM + dedup
│   │
│   ├── knowledge/
│   │   ├── __init__.py
│   │   ├── database.py            # Postgres connection + auto-bootstrap (schema separado)
│   │   ├── embeddings.py          # Embedding independente (cópia, suporta provider direto)
│   │   ├── search.py              # Hybrid search RRF pra knowledge
│   │   ├── ingest.py              # Chunking + contextual enrichment + store
│   │   └── loader.py              # File readers (PDF, MD, TXT, CSV)
│   │
│   ├── flows/                     # ⏳ PENDENTE (Fase 8)
│   │   └── __init__.py
│   │
│   ├── mcp/                       # ⏳ PENDENTE (Fase 9)
│   │   └── __init__.py
│   │
│   ├── cli/
│   │   ├── __init__.py
│   │   ├── main.py                # CLI: init/start/stop/test/list/chat/knowledge
│   │   └── templates/
│   │       ├── config.yaml        # Template com guardrails builtins visíveis
│   │       ├── env.example        # Template .env com TZ
│   │       ├── Dockerfile         # pip install do Git privado
│   │       ├── dockerignore
│   │       ├── gitignore
│   │       └── prompts/
│   │           └── system.md
│   │
│   ├── media/                     # ⏳ Pendente
│   │   └── __init__.py
│   │
│   └── queue/                     # ⏳ Pendente
│       └── __init__.py
│
├── clients/                       # ESPECÍFICO por agente (modo dev/repo)
│   ├── example/
│   ├── teste-hitl/
│   └── webhook-test/
│
├── tests/
│   ├── framework/
│   │   └── test_schema.py         # 5 passando, 2 falhando (desatualizados)
│   └── clients/
│
├── docs/
│   ├── DESENVOLVIMENTO-fase2-hitl.md
│   └── DESENVOLVIMENTO-fase3-habits.md
│
├── .github/
│   └── workflows/
│       └── publish.yml            # Tag push → build → PyPI (desativado, usando Git privado)
│
└── docker/
```

---

## LLM Provider — Auto-detecção

| Provider | .env necessário | URL usada |
|---|---|---|
| `bifrost` (default) | `BIFROST_URL` + `BIFROST_API_KEY` | Configurável |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| `gemini` | `GEMINI_API_KEY` | `https://generativelanguage.googleapis.com/v1beta/openai` |
| `deepseek` | `DEEPSEEK_API_KEY` | `https://api.deepseek.com/v1` |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| `custom` | `LLM_BASE_URL` + `LLM_API_KEY` | Configurável |

---

## Redis Keys

| Key | Tipo | TTL | Uso |
|---|---|---|---|
| `aleph:{cid}:spam:{msgId}` | String | 120s | Anti-spam messageId dedup |
| `aleph:{cid}:lock:{phone}` | String | 30s | Lock de processamento por phone |
| `aleph:{cid}:buffer:{phone}` | List | 13s | Mensagens picadas aguardando consolidação |
| `aleph:{cid}:ctx:{phone}` | String (JSON) | 3h | Contexto do cliente (nome, preferências) |
| `aleph:{cid}:takeover:{phone}` | String | 1h | Lock de transbordo humano |
| `aleph:{cid}:lid:{lid}` | String | 24h | LID → phone mapping |
| `aleph:{cid}:esc:{phone}` | String (JSON) | 2h | Sessão de escalation (EscalationData) |
| `aleph:{cid}:esc_msg:{msgId}` | String | 2h | Notification msgId → client phone |
| `aleph:{cid}:flow:{phone}` | String (JSON) | config | ⏳ Flow state (step atual + dados coletados) |

---

## Config YAML — Referência Rápida

### Mínimo funcional

```yaml
client_id: "meu-agente"
agent:
  name: "Meu Agente"
  model: "gpt-4.1-mini"
human:
  enabled: false
```

### Agente completo

```yaml
client_id: "padaria"

agent:
  name: "Atendente Padaria"
  model: "openai/gpt-4.1-mini"

human:
  enabled: true
  responsible_phones: ["5534999999999"]

guardrails:
  enable_fabrication_guard: true
  enable_price_leak_guard: false       # padaria precisa falar preços
  enable_ghost_escalation_guard: true
  input_patterns:
    - name: "cancelamento"
      keywords: ["cancelar", "quero cancelar"]
      action: "escalate"
      priority: 20
    - name: "reclamacao_grave"
      keywords: ["procon", "advogado"]
      action: "escalate_no_habit"
      priority: 30

knowledge:
  enabled: true
  auto_migrate: true
  schema: "knowledge"
  embedding_dimensions: 1536
  auto_search: true
  auto_search_top_k: 5

habits:
  enabled: true
  auto_migrate: true
  search_before_escalate: true

# flows:                              # ⏳ Fase 8
#   - name: "pedido"
#     trigger_keywords: ["quero pedir"]
#     steps:
#       - field: "sabor"
#         prompt: "Pergunte o sabor"
#         validation: "required"
#     on_complete:
#       action: "webhook"
#       webhook_url: "https://n8n.example.com/webhook/pedido"

messaging:
  disclaimer:
    enabled: true
    text: "Agente de atendimento Padaria"
```

---

## Instalação (Dev)

```bash
# Instalar framework (core only)
pip install git+https://TOKEN@github.com/nerdskips/aleph-framework.git

# Instalar com knowledge + habits (Postgres)
pip install "aleph-agent[knowledge] @ git+https://TOKEN@github.com/nerdskips/aleph-framework.git"

# Instalar tudo
pip install "aleph-agent[all] @ git+https://TOKEN@github.com/nerdskips/aleph-framework.git"

# Criar agente
aleph-agent init padaria --port 8010

# Validar
aleph-agent test padaria

# Chat interativo (terminal, sem WhatsApp)
aleph-agent chat padaria

# Knowledge base
aleph-agent knowledge load padaria --file data/cardapio.pdf
aleph-agent knowledge list padaria
aleph-agent knowledge clear padaria

# Deploy
aleph-agent start padaria
aleph-agent stop padaria
aleph-agent list
```

---

## Dependências

```
# Core (sempre instalado)
openai-agents>=0.12.0
fastapi, uvicorn, pydantic, pyyaml, python-dotenv
typer, rich
redis[hiredis], httpx

# Optional groups
[knowledge] → asyncpg, pypdf
[habits]    → asyncpg
[media]     → openai, pypdf
[queue]     → arq
[all]       → tudo acima
[dev]       → pytest, pytest-asyncio, ruff
```

---

*Aleph Framework — Álef Souza — 27/03/2026*