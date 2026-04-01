# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**Zuper/Aleph Framework** is a config-driven WhatsApp AI agent framework. Each agent is a folder under `clients/` containing a YAML config, system prompt, and optional custom tools — no changes to `core/` are ever needed.

**Stack:** Python 3.12, OpenAI Agents SDK 0.12.5, FastAPI, Redis, Postgres (pgvector), Z-API (WhatsApp gateway), Bifrost (LLM router)

## Commands

### Development
```bash
# Install in dev mode with all optional dependencies
pip install -e ".[dev,all]"

# Validate agent config
python -m core.registry.registry --client example --validate

# Interactive chat (no WhatsApp needed)
python -m core.engine.runner --client example --interactive

# Start webhook server
python -m core.api.webhooks --client example
```

### CLI (installed as `aleph-agent`)
```bash
aleph-agent init <name>                     # Create new agent scaffold
aleph-agent test <name>                     # Validate config + boot check
aleph-agent chat <name>                     # Interactive chat
aleph-agent start <name>                    # Docker build + run
aleph-agent stop <name>                     # Stop container
aleph-agent list                            # List agents in clients/
aleph-agent knowledge load <name> --file    # Ingest knowledge file
aleph-agent knowledge list <name>
aleph-agent knowledge clear <name>
```

### Linting
```bash
ruff check core/ tests/          # Check
ruff check --fix core/ tests/    # Auto-fix
```
Config: `line-length = 120`, `select = ["E", "F", "I", "N", "W"]`, `target-version = "py311"`

### Testing
```bash
pytest tests/                    # All tests
pytest tests/framework/          # Framework tests only
pytest -v tests/                 # Verbose
```
Config: `asyncio_mode = "auto"` (all test functions can be `async def`)

## Architecture

Three-layer design — the core principle is zero client code in `core/`:

1. **Agent layer** (`clients/<client_id>/`) — YAML config + prompts + custom tools + .env secrets
2. **Registry layer** (`core/registry/`) — YAML → Pydantic validation → runtime objects; no execution logic
3. **Core layer** (`core/`) — generic execution, never modified per client

### Message Processing Pipeline
```
Z-API webhook
  → Messaging filter (groups/broadcasts/reactions)
  → Human reply detection
  → Anti-spam (Redis dedup)
  → Message buffer (8s consolidation)
  → Processing lock (per phone, prevents race conditions)
  → Input guardrail (deterministic, pre-LLM) ← 9 possible actions
  → Knowledge search (hybrid RRF, if enabled)
  → Agent SDK run (primary model → fallback model)
  → Output guardrail (fabrication, price leak, ghost escalation)
  → Humanized send via Z-API
```

### Input Guardrail Actions (9 total)
| Action | Behavior | Uses LLM? |
|---|---|---|
| `continue` | Pass to agent | Yes |
| `redirect` | Reply directly, skip LLM | No |
| `block` | Safe response, no escalation | No |
| `inject` | Add instruction to system prompt | Yes |
| `escalate` | Check habits first, escalate if no match | Depends |
| `escalate_no_habit` | Always escalate | No |
| `takeover` | Pause bot, human assumes chat | No |
| `tool_required` | Force `tool_choice=<tool>` | Yes |
| `bypass_llm` | Skip LLM entirely (pending) | No |

### Key Modules
| Path | Purpose |
|---|---|
| `core/registry/schema.py` | All Pydantic config models |
| `core/registry/loader.py` | YAML → `FrameworkConfig` |
| `core/registry/registry.py` | Runtime object registry |
| `core/engine/pipeline.py` | Full message flow orchestration |
| `core/engine/runner.py` | OpenAI Agent SDK builder + executor |
| `core/guardrails/input.py` | Pre-LLM validation |
| `core/guardrails/output.py` | Post-LLM validation |
| `core/session/redis.py` | Buffer, anti-spam, lock, conversation context |
| `core/session/redis_escalation.py` | Escalation state |
| `core/human/escalation.py` | Pause, notify responsible, LLM reformulation |
| `core/llm/bifrost.py` | Multi-provider LLM routing + fallback |
| `core/messaging/zapi_filter.py` | Webhook parsing + filtering |
| `core/messaging/zapi_send.py` | Humanized message sending |
| `core/knowledge/` | RAG: asyncpg + pgvector, hybrid RRF search |
| `core/habits/` | Per-user memory, hybrid search |

## Development Rules

### Mandatory
- `from __future__ import annotations` must be the **first line** of every Python file (after module docstring if present)
- Redis keys always use prefix `aleph:{client_id}:` for multi-agent isolation
- New features must be **DEFAULT OFF** in the Pydantic schema, enabled via YAML
- All config and secrets come from YAML or `.env` — never hardcoded
- Loggers follow the pattern `logging.getLogger("aleph.modulename")`
- Optional dependencies (`asyncpg`, `pypdf`) must be **lazy-loaded** (imported inside functions)
- All I/O is async (Redis, Postgres, HTTP, LLM)

### Forbidden
- Modifying `core/` for client-specific logic
- Circular imports between core modules
- `print()` in production code — use `logger` instead

### Conventions
- Pydantic `Field()` with `description` and `default` for all schema fields
- `try/except` with logging — never silent failures

## YAML Config Structure

The top-level sections of `clients/<name>/config.yaml`:

```yaml
client_id: str
agent:        # Identity, model, temperature, system_prompt_file, parallel_tool_calls
sdk:          # sessions (Redis history), guardrails (tripwire), handoffs (max_turns)
debug:        # tracing, logging, dry_run
api:          # webhook port and path
human:        # enabled, responsible_phones, escalation_session_ttl
guardrails:   # input_patterns (keywords/regex → action), output_rules
knowledge:    # RAG (DEFAULT OFF) — auto_search, chunking, pgvector
habits:       # User memory (DEFAULT OFF) — dedup_threshold
tools:        # webhook (N8N/HTTP) or code tools from YAML
subagents:    # Specialist sub-agents invoked as tools (DEFAULT OFF) — Phase 10
queue:        # Background jobs after pipeline (DEFAULT OFF) — Phase 10
data_files:   # Static files injected into system prompt
```

See `clients/example/config.yaml` for a fully-annotated reference.

## Completed Phases

- **Phase 8** — Flows (state machine): `core/flows/`, Redis state, YAML-driven steps
- **Phase 9** — MCP Server: `core/mcp/server.py`, 8 tools, `aleph-mcp` CLI entry point
- **Phase 10** — Parallel Execution + Sub-Agents (branch: `feature/phase-10-parallel-subagents`):
  - **D1** `agent.parallel_tool_calls` → `ModelSettings(parallel_tool_calls=...)` in `core/llm/bifrost.py`
  - **D2** `subagents:` YAML section → `SubAgentConfig` → `agent.as_tool()` in `core/engine/runner.py`
  - **D3** `queue:` YAML section → `core/queue/` (jobs, dispatcher, worker) → fire-and-forget after pipeline

## Pending Implementations

### Phase 11 — Media Processing — LOWER PRIORITY
Whisper (audio), Vision (images), PDF handling in `core/media/` (currently empty stubs).

## Available MCPs
- `use context7` — fetch up-to-date library docs
- `sequential-thinking` — for complex architecture problems
- GitHub MCP — manage PRs and issues
- Postgres MCP — debug knowledge/habits database directly

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (90-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk vitest run          # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->