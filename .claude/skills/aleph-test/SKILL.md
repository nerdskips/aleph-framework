---
name: aleph-test
description: Test and validate Aleph Framework agents and code. Use when running tests, validating config, debugging agent behavior, writing pytest tests, or troubleshooting boot failures. Triggers on test creation, validation tasks, or debugging sessions.
---

# Aleph Test — Validation and Testing Patterns

## CLI Validation

```bash
# Validate agent config (no external connections needed)
aleph-agent test <agent-name>

# Interactive chat (needs LLM provider)
aleph-agent chat <agent-name>

# Chat with debug logs
aleph-agent chat <agent-name> --log-level DEBUG
```

## Writing pytest Tests

Tests go in `tests/framework/` or `tests/clients/`. Pattern:

```python
"""Test description."""
from __future__ import annotations

import pytest
from core.registry.schema import FrameworkConfig

def test_minimal_config():
    """Minimal config should load with all defaults."""
    config = FrameworkConfig(
        client_id="test",
        agent={"name": "Test", "model": "gpt-4.1-mini"},
    )
    assert config.client_id == "test"
    assert config.knowledge.enabled is False  # DEFAULT OFF

@pytest.mark.asyncio
async def test_knowledge_search():
    """Mock test for knowledge search."""
    # Always mock external deps (Redis, Postgres, LLM)
    pass
```

## Testing Rules

1. NEVER depend on external services in unit tests — mock everything
2. Use `pytest.mark.asyncio` for async tests
3. Test schema validation with both valid and invalid configs
4. Test guardrails with known input patterns
5. Test pipeline with mocked Redis/LLM/sender
6. Run with: `pytest tests/ -v`

## Common Debugging

```bash
# Check if YAML parses correctly
python -c "import yaml; print(yaml.safe_load(open('config.yaml')))"

# Check if schema validates
python -c "
from core.registry.schema import FrameworkConfig
import yaml
raw = yaml.safe_load(open('config.yaml'))
config = FrameworkConfig(**raw)
print('OK:', config.client_id)
"

# Check Redis connection
python -c "
import asyncio, redis.asyncio as r
async def t():
    c = r.from_url('redis://:pass@host:port/0')
    print(await c.ping())
asyncio.run(t())
"

# Check Postgres connection
python -c "
import asyncio, asyncpg
async def t():
    c = await asyncpg.connect('postgresql://user:pass@host:port/db')
    print(await c.fetchval('SELECT 1'))
asyncio.run(t())
"
```

## Checklist Before Deploy

- [ ] `aleph-agent test <name>` passes
- [ ] .env has real credentials (no placeholders)
- [ ] System prompt is meaningful (not placeholder)
- [ ] Guardrails configured for the business
- [ ] Knowledge base ingested (if enabled)
- [ ] `enable_price_leak_guard` set correctly
- [ ] Port doesn't conflict with other agents