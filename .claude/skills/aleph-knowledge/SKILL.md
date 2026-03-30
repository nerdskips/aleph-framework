---
name: aleph-knowledge
description: Manage Aleph Framework knowledge base (RAG). Use when ingesting documents, configuring knowledge search, debugging retrieval quality, chunking strategy, or embedding issues. Triggers on knowledge base setup, document ingestion, search quality problems, or RAG configuration.
---

# Aleph Knowledge — RAG Management Patterns

## CLI Commands

```bash
# Ingest a single file
aleph-agent knowledge load <agent> --file data/cardapio.pdf

# Ingest all files in a directory
aleph-agent knowledge load <agent> --dir data/

# List what's in the knowledge base
aleph-agent knowledge list <agent>

# Clear all entries
aleph-agent knowledge clear <agent>

# Clear entries from a specific source
aleph-agent knowledge clear <agent> --source cardapio.pdf
```

## Supported File Formats

- `.pdf` — requires `pypdf` (install with `pip install aleph-agent[knowledge]`)
- `.md` — markdown files
- `.txt` — plain text
- `.csv` — converted to "key: value" per row

## YAML Configuration

```yaml
knowledge:
  enabled: true
  auto_migrate: true              # creates schema/table/indexes on startup
  schema: "knowledge"             # Postgres schema (separate from habits)
  table_name: "knowledge_base"
  embedding_model: "text-embedding-3-small"
  embedding_dimensions: 1536      # MUST match model output dims
  auto_search: true               # search before every LLM call
  auto_search_top_k: 5            # chunks to inject
  tool_search: true               # also expose as tool for agent
  similarity_threshold: 0.7
  match_count: 5
  rrf_k: 60                       # RRF ranking constant
  chunk_size: 500                  # chars per chunk
  chunk_overlap: 75               # chars overlap between chunks
```

## Architecture

```
Ingestion: file → loader → recursive chunking → contextual enrichment → embedding → Postgres
Runtime:   query → embedding → hybrid search RRF (semantic + fulltext) → top-k → inject pre-LLM
```

- Postgres table: `{schema}.knowledge_base`
- Hybrid search RRF function: `buscar_conhecimento_hibrido`
- tsvector trigger: auto-updates on INSERT/UPDATE (context=weight A, content=weight B)
- Embeddings: independent module (`core/knowledge/embeddings.py`)
- Supports Bifrost or direct API key (auto-detects from .env)

## Chunking Strategy

- Recursive splitting: paragraphs → lines → sentences → hard split
- Contextual enrichment: section headers detected and prepended to chunks
- Embedding includes context + content concatenated
- Small documents (< chunk_size) stay as single chunk — this is correct

## Troubleshooting

- **"No embedding provider found"**: Set BIFROST_URL or OPENAI_API_KEY in .env
- **UTF-8 errors**: Content has invalid encoding. The search sanitizes with `encode('utf-8', errors='replace')`
- **"another operation in progress"**: asyncpg pool conflict — ensure single event loop (use `_chat_loop` pattern)
- **Zero results**: Check `aleph-agent knowledge list` to confirm chunks exist
- **Low relevance**: Try lower `similarity_threshold` or different `chunk_size`
- **Dimensions mismatch**: `embedding_dimensions` must match the model you're using