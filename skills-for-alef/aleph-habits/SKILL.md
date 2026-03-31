---
name: aleph-habits
description: Use when configuring or debugging the habits system in the Aleph Framework. Covers per-user memory storage, dedup threshold, hybrid search, how habits are injected into the LLM context, and YAML config.
---

# Aleph Habits

## What it is

Per-user persistent memory. Every conversation exchange can be stored as a "habit" — a short-term behavioral pattern — and retrieved for future sessions via hybrid semantic + keyword search. The LLM receives relevant past habits as context before generating a response.

Habits are stored in Postgres (pgvector), scoped per `(client_id, phone)`.

---

## Enable

```yaml
habits:
  enabled: true
  postgres_url: "${POSTGRES_URL}"
```

`enabled` defaults to `false`.

---

## Full schema

```yaml
habits:
  enabled: true
  postgres_url: "${POSTGRES_URL}"     # required — same DB as knowledge if using both
  table_name: habits                   # default
  schema: public                       # default
  dedup_threshold: 0.92                # cosine similarity — skip storing if too similar to existing
  search_limit: 5                      # max habits retrieved per message (default: 5)
  search_threshold: 0.65               # min similarity to include in context (default: 0.65)
  embedding_model: text-embedding-3-small   # default
  ttl_days: 90                         # auto-expire old habits (default: 90, 0 = never)
```

---

## dedup_threshold

The most important tuning parameter. Before storing a new habit, the framework checks if a semantically similar one already exists for this user:

| Value | Behavior |
|---|---|
| `0.95+` | Very strict — almost nothing is deduplicated. Memory grows fast. |
| `0.90–0.92` | Default range — good balance for most agents |
| `0.80–0.85` | Aggressive dedup — only clearly different habits are stored |
| `< 0.80` | Too aggressive — you'll lose distinct memories |

If the user asks the same question repeatedly, you don't want 50 near-identical habits — tune this.

---

## search_threshold

Controls what gets injected into the LLM context:

- Too low (< 0.5) → irrelevant habits pollute the context
- Too high (> 0.80) → almost nothing is ever retrieved

Default `0.65` works for most cases. Lower it if the agent feels like it "forgets" the user; raise it if responses feel cluttered with unrelated history.

---

## What gets stored as a habit

The framework stores the **full exchange** (user message + agent response) after each successful LLM call. The embedding is computed on the combined text.

You don't configure what gets stored — it's automatic when habits is enabled. The dedup check prevents redundant entries.

---

## How habits are injected

Before the LLM call, the framework searches for relevant habits using hybrid RRF (dense vector + BM25 keyword). Matches above `search_threshold` are prepended to the system prompt as:

```
[Histórico relevante deste usuário]
- Usuário perguntou X, respondi Y (há 3 dias)
- Usuário mencionou preferência por Z (há 1 semana)
```

The LLM then has this context when generating the response.

---

## Habits vs Knowledge

| | Habits | Knowledge |
|---|---|---|
| Scope | Per user (phone) | Global (all users) |
| Source | Conversation history | Ingested documents |
| Storage | pgvector, auto-created | pgvector, manually ingested |
| Use case | Personalization, memory | FAQ, product info, RAG |

Both can be enabled simultaneously.

---

## Checking habit state

Via MCP (Postgres MCP):
```sql
SELECT phone, content, created_at
FROM habits
WHERE client_id = 'my-agent'
ORDER BY created_at DESC
LIMIT 20;
```

---

## Common mistakes

| Mistake | Fix |
|---|---|
| Habits enabled but no `POSTGRES_URL` | Will fail at boot — set in `.env` |
| `dedup_threshold: 1.0` | Stores everything — DB grows unbounded |
| `search_threshold: 0.3` | LLM gets flooded with vaguely related history |
| Same DB as knowledge with same `table_name` | Set different `table_name` for each — they share the schema but need separate tables |
| `ttl_days: 0` and never cleaning | Habits accumulate forever — set a reasonable TTL |
