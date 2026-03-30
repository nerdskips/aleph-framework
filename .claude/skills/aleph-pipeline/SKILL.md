---
name: aleph-pipeline
description: Modify the Aleph Framework execution pipeline. Use when adding new steps to pipeline.py, wiring new features into the message processing flow, or debugging pipeline execution order. Triggers on pipeline changes, new pre-LLM or post-LLM steps, or flow modifications.
---

# Aleph Pipeline — Execution Flow Patterns

The pipeline in `core/engine/pipeline.py` is the heart of the framework. Every message flows through it.

## Current Execution Order

```
1. Input guardrail (deterministic, pre-LLM, zero cost)
   → redirect/block: return immediately
   → inject: modify user_message
   → escalate: search habits → escalate if no match
   → escalate_no_habit: always escalate

1.5. Check active escalation (skip if already escalated)

1.8. Knowledge search (if knowledge.auto_search=true)
     → inject context into user_message

[PENDING] 1.9. Flow check (if flow active for this phone)

2. Run agent (LLM with fallback)

3. Output guardrail (post-LLM validation)
   → fabrication, price_leak, ghost_escalation, custom regex
```

## Adding a New Pre-LLM Step

Insert between existing numbered steps. Pattern:

```python
    # ---------------------------------------------------------------
    # 1.X Description of new step
    # ---------------------------------------------------------------
    if config.my_feature.enabled and my_feature_db:
        try:
            from core.my_feature.module import my_function

            result = await my_function(
                db=my_feature_db,
                config=config.my_feature,
                client_id=config.client_id,
                query=user_message,
            )

            if result:
                user_message = f"{user_message}\n\n{result}"
                logger.info("Pipeline: my_feature context injected (%d chars)", len(result))

        except Exception as e:
            logger.error("Pipeline: my_feature failed: %s", str(e)[:200])
```

## Rules

1. NEVER crash the pipeline — always try/except with logger.error
2. Failed steps fall through silently (degrade gracefully)
3. New steps that need DB connections must be initialized in `webhooks.py` lifespan
4. Pass new DB instances through `process_message()` parameters
5. Pre-LLM steps modify `user_message` string
6. Post-LLM steps check/modify `response` string
7. `PipelineResult` tracks metadata (skipped_llm, escalated, habit_used, etc)
8. The `process_message()` function signature must stay backward-compatible (new params have defaults)

## Wiring in webhooks.py

When adding a new database-backed feature:

1. Add `_my_feature_db = None` to global state
2. Add to `global` declaration in `lifespan()`
3. Initialize in lifespan after existing inits
4. Pass to `process_message()` in `_process_after_buffer()`
5. Close in lifespan cleanup