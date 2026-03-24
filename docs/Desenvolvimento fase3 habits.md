# Zuper Agent Framework — Fase 3: Hábitos Operacionais

> Atualizado em 24/03/2026 — Sessão de desenvolvimento com Claude Opus 4.6
> Repo: `/root/zuper-framework/` no server Hostinger KVM2

---

## Resumo

Implementação dos hábitos operacionais: o framework aprende com resoluções humanas de escalation e reutiliza esse conhecimento para responder perguntas similares sem escalonar novamente.

**Busca:** Hybrid search RRF (tsvector + pgvector) em Postgres genérico.
**Banco:** Qualquer Postgres com pgvector (self-hosted, Supabase, RDS, etc).
**Bootstrap:** Automático na primeira subida (extensões, tabela, indexes, functions).

---

## Decisões de Design

### Dois tipos de hábito

| Tipo | Flag | Exemplo | Busca retorna? |
|---|---|---|---|
| **GERAL** | `is_unique=false` | "Horário no carnaval: sábado 9-14h" | Sim |
| **ÚNICO** | `is_unique=true` | "Pedido #4521 do João veio errado" | Não |

**LLM classifica na gravação** (zero custo na busca).

### Embedding

- Gerado **só da pergunta generalizada** (não inclui resposta)
- tsvector inclui pergunta (weight A) + resposta (weight B) via trigger
- Hybrid search combina os dois canais via RRF

### Dedup

- Cosine distance < 0.020 = duplicata (production-validated)
- Só checa hábitos GERAIS
- Se duplicata encontrada, não grava

### Banco genérico

- `DATABASE_URL` no `.env` (queries do dia a dia)
- `DATABASE_MIGRATION_URL` opcional (DDL — necessário pra Supabase pooler)
- `auto_migrate=true` cria tudo na subida

---

## Arquitetura

### Gravação (após resolução de escalation)

```
Humano resolve escalation
  → LLM classifica: GERAL ou ÚNICO
  → Se GERAL: LLM generaliza pergunta e resposta
  → Gera embedding da pergunta generalizada
  → Dedup check (cosine distance < 0.020)
  → Se não duplicata: INSERT no Postgres
  → Trigger auto-popula tsvector (pergunta=A, resposta=B)
```

### Busca (antes de escalonar)

```
Guardrail detecta ESCALATE + habits.search_before_escalate=true
  → Gera embedding da pergunta do cliente
  → Chama buscar_habito_hibrido() no Postgres
    → Semantic: pgvector cosine similarity (is_unique=false)
    → Fulltext: tsvector Portuguese stemming + unaccent (is_unique=false)
    → RRF: combina ranks com k=60
  → Se match encontrado: injeta no prompt do LLM, skip escalation
  → Se sem match: escalona normalmente
```

---

## Arquivos Criados

| Arquivo | Descrição |
|---|---|
| `core/habits/__init__.py` | Exports do módulo |
| `core/habits/database.py` | Conexão Postgres + auto-bootstrap completo |
| `core/habits/embeddings.py` | Geração de embedding via Bifrost |
| `core/habits/store.py` | Gravação com classificação LLM + dedup |
| `core/habits/search.py` | Hybrid search RRF + formatação pra injeção |
| `core/habits/schema_diff.py` | Campos novos pro `HabitsConfig` (auto_migrate, schema) |

## Arquivos a Modificar

| Arquivo | O que mudar |
|---|---|
| `core/registry/schema.py` | Substituir `HabitsConfig` pela versão com `auto_migrate` e `schema` |
| `core/engine/pipeline.py` | Wiring do `search_before_escalate` antes do ESCALATE |
| `core/human/escalation.py` | Chamar `store_habit()` após resolução |
| `core/api/webhooks.py` | Inicializar `HabitsDatabase` no lifespan, passar pro pipeline |

---

## Schema do Banco

### Extensões

```sql
CREATE EXTENSION IF NOT EXISTS vector;     -- pgvector
CREATE EXTENSION IF NOT EXISTS unaccent;   -- normalização de acentos
```

### Tabela: operational_habits

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | BIGSERIAL PK | Auto-increment |
| `client_id` | TEXT NOT NULL | Isolamento por agente |
| `question` | TEXT NOT NULL | Pergunta (generalizada se GERAL) |
| `answer` | TEXT NOT NULL | Resposta (generalizada se GERAL) |
| `human_instruction` | TEXT | Instrução original do humano (sempre preservada) |
| `is_unique` | BOOLEAN | false=GERAL (buscável), true=ÚNICO (só histórico) |
| `embedding` | vector(1536) | Embedding da pergunta generalizada |
| `search_text` | TSVECTOR | Auto-populado por trigger (question=A, answer=B) |
| `metadata` | JSONB | Contexto, tags, etc |
| `created_at` | TIMESTAMPTZ | Auto |
| `updated_at` | TIMESTAMPTZ | Auto (trigger) |

### Indexes

| Nome | Tipo | Coluna | Propósito |
|---|---|---|---|
| `idx_*_search_text` | GIN | search_text | Full-text search |
| `idx_*_embedding` | IVFFlat | embedding (cosine) | Semantic search |
| `idx_*_client_id` | btree | client_id | Filtro por agente |
| `idx_*_is_unique` | btree | is_unique | Filtro GERAL vs ÚNICO |

### Function: buscar_habito_hibrido

```sql
buscar_habito_hibrido(
    p_client_id TEXT,
    p_query TEXT,
    p_embedding vector(1536),
    p_match_count INT DEFAULT 3,
    p_rrf_k INT DEFAULT 60
) RETURNS TABLE (id, question, answer, human_instruction, metadata, rrf_score, semantic_rank, fulltext_rank)
```

**Algoritmo RRF:**
1. Semantic: top N*3 por cosine similarity (is_unique=false)
2. Fulltext: top N*3 por ts_rank_cd com Portuguese stemmer + unaccent (is_unique=false)
3. FULL OUTER JOIN: combina os dois rankings
4. RRF score: `1/(k+rank_sem) + 1/(k+rank_ft)`
5. Retorna top N por RRF score

### Trigger: auto-update search_text

```sql
-- Executa BEFORE INSERT OR UPDATE
NEW.search_text :=
    setweight(to_tsvector('portuguese', unaccent(question)), 'A') ||
    setweight(to_tsvector('portuguese', unaccent(answer)), 'B');
```

---

## Config YAML

### Campos novos no HabitsConfig

```yaml
habits:
  enabled: true
  auto_migrate: true              # NOVO — cria tabela/functions na subida (default true)
  schema: "public"                # NOVO — Postgres schema (default "public")
  embedding_model: "openai/text-embedding-3-small"
  embedding_dimensions: 1536
  dedup_threshold: 0.020
  rrf_k: 60
  match_count: 3
  search_before_escalate: true
  table_name: "operational_habits"
```

### .env

```env
# Postgres self-hosted
DATABASE_URL=postgresql://zuper:senha@10.0.0.5:5432/zuper_agents

# Supabase (pooler pra queries)
DATABASE_URL=postgresql://postgres.xxxx:senha@aws-0-sa-east-1.pooler.supabase.com:6543/postgres
# Supabase (direto pra migrations — necessário se usa pooler)
DATABASE_MIGRATION_URL=postgresql://postgres.xxxx:senha@db.xxxx.supabase.co:5432/postgres
```

### .env.example (adicionar)

```env
# --- Database (required when habits.enabled=true) ---
# DATABASE_URL=postgresql://user:pass@host:5432/dbname
# DATABASE_MIGRATION_URL=                              # Optional: direct connection for DDL (Supabase pooler)
```

---

## Exemplo Completo de Config

```yaml
client_id: "padaria"

agent:
  name: "Laura"
  model: "openai/gpt-4.1-mini"

human:
  enabled: true
  responsible_phones: ["5534999999999"]

habits:
  enabled: true
  search_before_escalate: true
  # auto_migrate: true     ← default
  # schema: "public"       ← default
  # dedup_threshold: 0.020 ← default
  # match_count: 3         ← default

guardrails:
  input_patterns:
    - name: "cancelamento"
      keywords: ["cancelar", "quero cancelar"]
      action: "escalate"        # Com habits: busca primeiro, escalona se não achar
      priority: 20
    - name: "reclamacao_grave"
      keywords: ["procon", "processo"]
      action: "escalate_no_habit"  # SEMPRE escalona, nunca consulta hábitos
      priority: 30
```

---

## Wiring Pendente

### 1. Pipeline — search_before_escalate

No `pipeline.py`, ANTES de escalonar, se `habits.enabled` e `search_before_escalate`:

```python
# Dentro de _handle_escalation, antes de chamar escalate_to_human:
if config.habits.enabled and config.habits.search_before_escalate:
    if classification.action != GuardrailAction.ESCALATE_NO_HABIT:
        habit_context = await search_and_format(habits_db, config.habits, client_id, user_message)
        if habit_context:
            # Hábito encontrado! Injeta no prompt e vai pro LLM
            user_message = f"{user_message}\n\n{habit_context}"
            # NÃO escalona — segue pro LLM com o contexto
            return None  # sinaliza pro pipeline continuar pro LLM
```

### 2. Escalation — store após resolução

No `escalation.py`, DEPOIS de enviar resposta ao cliente:

```python
# Dentro de handle_human_response, após send_response:
if registry.config.habits.enabled:
    await store_habit(
        db=habits_db,
        config=registry.config.habits,
        registry=registry,
        client_id=registry.config.client_id,
        original_question=esc_data.original_message,
        human_instruction=human_instruction,
        metadata=esc_data.context,
    )
```

### 3. Webhooks — inicializar HabitsDatabase

No `webhooks.py` lifespan:

```python
# Após Redis connect:
if _registry.config.habits.enabled:
    _habits_db = HabitsDatabase(_registry.config.habits)
    await _habits_db.connect()
    await _habits_db.bootstrap()
```

---

## Fluxo Completo: Escalate → Habit → Reutilizar

```
Dia 1:
  Cliente pergunta "horário no carnaval?" → ESCALATE
  → Habits search: 0 matches → escalona
  → Humano: "sábado das 9 às 14"
  → LLM reformula → envia ao cliente
  → LLM classifica: GERAL
  → Generaliza: "horário de funcionamento no carnaval"
  → Grava hábito

Dia 2:
  Outro cliente: "vocês abrem no carnaval?"
  → ESCALATE
  → Habits search: match! score=0.89
  → Injeta contexto no LLM
  → LLM responde com a info do hábito
  → NÃO escalona (humano nem recebe notificação)
```

---

## Testes Necessários

### Unitários

- [ ] LLM classifica GERAL vs ÚNICO corretamente
- [ ] Generalização remove dados específicos
- [ ] Dedup detecta hábitos similares
- [ ] `search_and_format` retorna contexto formatado

### Integração (precisa Postgres + pgvector)

- [ ] Bootstrap cria tabela/indexes/function
- [ ] Bootstrap é idempotente (rodar 2x não quebra)
- [ ] Insert popula tsvector via trigger
- [ ] Hybrid search retorna resultados corretos
- [ ] Filtro is_unique=false funciona (ÚNICO não aparece)
- [ ] Dedup threshold bloqueia duplicatas

### E2E (precisa Postgres + Redis + Bifrost)

- [ ] Escalation → resolução → hábito gravado
- [ ] Segunda pergunta similar → hábito encontrado → sem escalation
- [ ] ESCALATE_NO_HABIT → sempre escalona mesmo com hábito

---

## Dependência Nova

```bash
pip install asyncpg
```

Adicionar no `pyproject.toml`:

```toml
dependencies = [
    # ... existentes ...
    "asyncpg>=0.29.0",
]
```

---

*Zuper AI — Álef Souza — 24/03/2026*