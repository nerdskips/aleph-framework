# Phase 15 — Flow Engine v2: Conditional, Validated, Dynamic Flows

> **Status:** In progress — design finalized 2026-04-11
>
> **Goal:** Upgrade the flow engine from a rigid script runner to an enterprise-grade conditional workflow engine. Adds lookup steps (external API/CRM mid-flow), branch steps (conditional jumps), per-step tool-based validation with LLM injection, variable templating, sensitive field marking, step timeouts, and webhook retry with backoff.

## Design decisions (finalized 2026-04-11)

| Decision | Choice | Rationale |
|---|---|---|
| `set` step | **DROPPED** | Tool use already handles data transformation — redundant |
| Validation approach | **Tool-based only** (`validate.tool`) | Developer configures a tool; engine calls it with the collected answer; no hardcoded regex/type/webhook in the engine |
| Validation tool return convention | **Option A**: `{"valid": bool, "message": str}` | Simple, predictable contract |
| Validation error response | **LLM injection** | On `valid=false`, tool's `message` is injected into the LLM context so the agent responds naturally — no brick-like automated messages |
| Lookup `on_error` default | `escalate` | Safe default: don't silently swallow CRM failures |

---

## Why this phase exists

The current engine (Phase 8) walks steps sequentially with no mid-flow branching, no external lookups, and no answer validation. Real enterprise flows need:

- **CRM lookup mid-flow** — check customer type before deciding which path to take
- **Conditional branching** — Type A customer skips payment step, Type B doesn't
- **Answer validation** — reject "banana" as a CPF before accepting it and moving on
- **Variable templating** — show the customer's actual service fee, not a hardcoded string
- **Sensitive fields** — CPF, card numbers, passwords must not appear in logs or episodic memory
- **Step timeouts** — user went silent mid-flow, re-ask or cancel gracefully
- **Webhook retry** — don't silently swallow a failed CRM call

None of these require touching the pipeline, runner, or agent. Everything is schema + engine changes.

---

## Architecture overview

### New step types

```
message   (existing) — ask user, collect answer, advance
lookup    (NEW)      — call external API, store result, no user interaction
branch    (NEW)      — evaluate condition, jump to step, no user interaction
```

> `set` step was dropped — tool use already handles data transformation.

### New cross-cutting features

```
validate         — per message-step: regex/webhook/type check before accepting answer
skip_if          — per step: skip this step if condition is true
sensitive        — per step: exclude collected value from logs + episodic memory
step_timeout     — per step or flow-level default: re-ask or cancel after N minutes
cancel_if        — per flow: abort entire flow if message matches pattern
{{ templating }} — interpolate collected.field in all string fields
webhook retry    — per lookup/on_complete webhook: exponential backoff, max attempts
```

---

## Schema additions (`core/registry/schema.py`)

### `StepType` enum (new)

```python
class StepType(str, Enum):
    message = "message"   # default: ask user, collect answer
    lookup  = "lookup"    # call external API, no user interaction
    branch  = "branch"    # evaluate condition, jump to step
    set     = "set"       # assign computed value to variable
```

### `StepValidation` model (new)

```python
class StepValidation(BaseModel):
    """Per-step answer validation. Only applies to type=message steps."""
    regex: str = Field("", description="Regex the answer must match")
    type: str = Field("", description="Type check: 'int', 'float', 'date', 'cpf', 'email', 'phone'")
    webhook: str = Field("", description="URL to POST answer to — 200 = valid, 4xx = invalid")
    error_message: str = Field(
        "Resposta inválida. Por favor, tente novamente.",
        description="Message sent to user when validation fails",
    )
    max_retries: int = Field(2, ge=0, le=10, description="Max retries before on_exceed fires")
    on_exceed: str = Field("escalate", description="Action after max_retries: escalate | cancel | send_message")
    exceed_message: str = Field("", description="Message for on_exceed=send_message")
```

### `BranchCondition` model (new)

```python
class BranchCondition(BaseModel):
    """Single condition in a branch step."""
    if_expr: str = Field("", alias="if", description="Condition expression: 'collected.field == value'")
    jump_to: str = Field("", description="Step ID to jump to if condition is true")
    else_jump: str = Field("", alias="else", description="Fallback step ID (last condition only)")
```

### `LookupConfig` model (new)

```python
class LookupConfig(BaseModel):
    """Webhook call config for lookup steps."""
    url: str = Field(..., description="Endpoint URL (supports {{ templating }})")
    method: str = Field("POST", description="HTTP method: GET | POST")
    payload: dict = Field(default_factory=dict, description="Request body (supports {{ templating }} in values)")
    headers: dict = Field(default_factory=dict, description="Extra HTTP headers")
    timeout_seconds: int = Field(10, ge=1, le=60, description="Request timeout")
    response_key: str = Field("", description="Dot-path into response JSON to extract (e.g. 'data.customer_type')")
    on_error: str = Field("escalate", description="Action on HTTP error/timeout: escalate | cancel | jump_to | continue")
    error_jump: str = Field("", description="Step ID for on_error=jump_to")
    retry_attempts: int = Field(2, ge=0, le=5, description="Retry attempts on 5xx/timeout")
    retry_backoff_seconds: float = Field(1.5, description="Base seconds for exponential backoff between retries")
```

### `StepConfig` additions

Extend the existing `StepConfig` model with:

```python
# Step type
type: StepType = Field(StepType.message, description="Step execution type")

# Validation (message steps only)
validate: StepValidation | None = Field(None, description="Answer validation rules")

# Lookup (lookup steps only)
lookup: LookupConfig | None = Field(None, description="External API call config")

# Branch (branch steps only)
conditions: list[BranchCondition] = Field(default_factory=list, description="Branch conditions (evaluated in order)")

# Set (set steps only)
set_expr: str = Field("", description="Expression to evaluate: 'collected.price * 1.1'")

# Universal
skip_if: str = Field("", description="Skip this step if expression is true")
sensitive: bool = Field(False, description="Exclude collected value from logs and episodic memory")
step_timeout_minutes: float = Field(0.0, ge=0, description="Override flow-level step timeout. 0 = use flow default")
```

### `FlowDefinition` additions

```python
# New flow-level fields
cancel_if: list[str] = Field(default_factory=list, description="Regex/keyword patterns that abort the entire flow")
on_cancel: str = Field("send_message", description="Action on cancel_if match: send_message | escalate | none")
cancel_message: str = Field("Tudo bem! Se precisar de mais ajuda, é só chamar.", description="Message for on_cancel=send_message")
default_step_timeout_minutes: float = Field(0.0, ge=0, description="Default step timeout. 0 = disabled")
default_timeout_action: str = Field("re_ask", description="re_ask | cancel | escalate")
default_timeout_message: str = Field("", description="Re-ask message. Empty = repeat original step message")
```

### `FlowState` additions

```python
# Track validation retries per step
retry_counts: dict[str, int] = Field(default_factory=dict, description="step_id → retry count")
```

---

## Engine changes (`core/flows/engine.py`)

### Processing order within `resolve()`

```
1. check cancel_if patterns → abort if matched
2. check step timeout → re-ask/cancel/escalate if exceeded
3. [existing] no active flow → check triggers → start
4. [existing] off-topic detection → hold/pause
5. [NEW] current step type dispatch:
   - message → validate answer → collect or retry
   - lookup  → call API → store result → auto-advance
   - branch  → evaluate conditions → jump
   - set     → evaluate expression → store → auto-advance
6. [existing] advance or complete
```

### Step type: `lookup`

```python
async def _execute_lookup(self, step, state, redis_session, phone) -> str:
    """Call external API. Returns next step ID or raises LookupError."""
    # 1. Template the URL and payload with collected values
    # 2. Call webhook with retry/backoff
    # 3. Extract response_key from JSON response
    # 4. Store in state.collected[step.collect_as]
    # 5. Return step.next
```

Lookup steps are invisible to the user — the engine calls them, stores the result, and immediately resolves the next step (which may be another lookup, a branch, or a message).

### Step type: `branch`

```python
def _evaluate_branch(self, step, state) -> str:
    """Evaluate conditions in order. Return the jump_to step ID."""
    for condition in step.conditions:
        if condition.if_expr and _eval_expr(condition.if_expr, state.collected):
            return condition.jump_to
        if condition.else_jump:
            return condition.else_jump
    # No match, no else → log warning, complete flow
    logger.warning("Branch '%s' had no matching condition and no else clause", step.id)
    return ""
```

### Expression evaluator (`_eval_expr`)

A safe, sandboxed evaluator — no `eval()`. Supports:

```
collected.field == "value"
collected.field != "value"
collected.field in ["a", "b", "c"]
collected.field > 100
collected.field starts_with "prefix"
collected.field is_empty
collected.field is_not_empty
```

Implemented as a simple parser over a restricted grammar — no arbitrary Python execution.

### Validation loop

```python
async def _validate_answer(self, step, message, state, phone) -> tuple[bool, str]:
    """Returns (is_valid, error_message). Increments retry count on failure."""
    retries = state.retry_counts.get(step.id, 0)

    if step.validate.regex and not re.fullmatch(step.validate.regex, message):
        state.retry_counts[step.id] = retries + 1
        return False, step.validate.error_message

    if step.validate.type:
        if not _check_type(step.validate.type, message):
            state.retry_counts[step.id] = retries + 1
            return False, step.validate.error_message

    if step.validate.webhook:
        ok = await _call_validation_webhook(step.validate.webhook, message)
        if not ok:
            state.retry_counts[step.id] = retries + 1
            return False, step.validate.error_message

    return True, ""
```

### Variable templating (`_render`)

```python
def _render(template: str, collected: dict) -> str:
    """Interpolate {{ collected.field }} and {{ collected.nested.field }} in a string."""
    # Simple regex-based replacement — no Jinja2 dependency
    def replacer(match):
        key_path = match.group(1).strip()  # e.g. "collected.customer.type"
        parts = key_path.split(".")
        val = collected
        for part in parts[1:]:  # skip "collected"
            if isinstance(val, dict):
                val = val.get(part, "")
            else:
                return ""
        return str(val)
    return re.sub(r"\{\{\s*([\w.]+)\s*\}\}", replacer, template)
```

Applied to: step messages, webhook URLs, webhook payload values, cancel_message, timeout_message.

### Sensitive field handling

When `step.sensitive = True`:
- Value is stored in `state.collected` normally (needed for flow logic)
- Excluded from pipeline's `flow_collected` dict (which goes to episodic memory + escalation)
- Logged as `"[REDACTED]"` in all log calls

---

## YAML example — full CRM flow

```yaml
flows:
  enabled: true
  flows:
    - id: atendimento
      trigger_keywords: ["atendimento", "suporte", "ajuda"]
      cancel_if:
        - "cancelar"
        - "desistir"
        - "não quero mais"
      on_cancel: send_message
      cancel_message: "Tudo bem! Se precisar de ajuda futuramente, é só chamar."
      default_step_timeout_minutes: 30
      default_timeout_action: re_ask
      on_complete:
        action: webhook
        url: "https://crm.company.com/api/ticket"
        method: POST
        retry_attempts: 3

      steps:
        - id: ask_cpf
          message: "Para começar, me informe seu CPF (somente números)."
          collect_as: cpf
          sensitive: true
          validate:
            type: cpf
            error_message: "CPF inválido. Por favor, tente novamente com 11 dígitos."
            max_retries: 2
            on_exceed: escalate

        - id: lookup_customer
          type: lookup
          collect_as: customer
          lookup:
            url: "https://crm.company.com/api/customer/lookup"
            method: POST
            payload:
              cpf: "{{ collected.cpf }}"
            timeout_seconds: 8
            response_key: "data"
            on_error: jump_to
            error_jump: crm_unavailable
            retry_attempts: 2
          next: route_by_type

        - id: route_by_type
          type: branch
          conditions:
            - if: "customer.status == 'inactive'"
              jump_to: inactive_customer
            - if: "customer.type == 'A'"
              jump_to: welcome_free
            - if: "customer.type == 'B'"
              jump_to: ask_payment_consent
            - else: unknown_customer

        - id: welcome_free
          message: "Olá, {{ customer.name }}! Seu plano inclui atendimento gratuito. Como posso te ajudar hoje?"
          collect_as: issue_description
          next: confirm_ticket

        - id: ask_payment_consent
          message: "Olá, {{ customer.name }}! Seu atendimento tem custo de R$ {{ customer.service_fee }}. Deseja continuar?"
          collect_as: payment_consent
          validate:
            regex: "(?i)(sim|não|nao|yes|no)"
            error_message: "Por favor, responda 'sim' ou 'não'."
            max_retries: 2
            on_exceed: cancel
          next: check_payment_consent

        - id: check_payment_consent
          type: branch
          conditions:
            - if: "payment_consent in ['sim', 'yes', 's']"
              jump_to: ask_issue
            - else: payment_declined

        - id: ask_issue
          message: "Perfeito! Descreva seu problema e nossa equipe entrará em contato."
          collect_as: issue_description
          next: confirm_ticket

        - id: confirm_ticket
          message: "Ticket aberto com sucesso! Nossa equipe responderá em até 2 horas."

        - id: payment_declined
          message: "Sem problemas! Se mudar de ideia, é só chamar."

        - id: inactive_customer
          message: "Seu cadastro está inativo. Para reativá-lo, acesse {{ customer.reactivation_url }}."

        - id: unknown_customer
          message: "Não encontrei seu cadastro. Vou conectar você com um atendente."
          next: escalate_step

        - id: crm_unavailable
          message: "Nosso sistema está temporariamente indisponível. Um atendente irá te ajudar."

        - id: escalate_step
          message: "Transferindo para atendimento humano..."
          on_complete:
            action: escalate
```

---

## File map

| Action | File | Responsibility |
|---|---|---|
| MODIFY | `core/registry/schema.py` | Add `StepType`, `StepValidation`, `BranchCondition`, `LookupConfig`; extend `StepConfig`, `FlowDefinition`, `FlowState` |
| MODIFY | `core/flows/engine.py` | Add `lookup`, `branch`, `set` dispatch; validation loop; cancel_if; timeout check; `_eval_expr`; `_render` |
| MODIFY | `core/flows/state.py` | Add `retry_counts` field |
| CREATE | `core/flows/expr.py` | Safe expression evaluator (`_eval_expr`) |
| CREATE | `core/flows/template.py` | Variable interpolation (`_render`) |
| MODIFY | `clients/example/config.yaml` | Full annotated flow example with new step types |
| MODIFY | `CLAUDE.md` | Document Phase 15 |
| MODIFY | `README.md` | Update flows YAML reference |
| CREATE | `tests/framework/test_flow_engine_v2.py` | Full coverage: lookup, branch, validation, timeout, cancel_if, templating |

---

## Task breakdown

### Task 1 — Schema: new step types + validation fields
- Add `StepType` enum
- Add `StepValidation`, `BranchCondition`, `LookupConfig` models
- Extend `StepConfig` with new fields (all optional, backward-compatible)
- Extend `FlowDefinition` with `cancel_if`, timeout defaults
- Extend `FlowState` with `retry_counts`
- Tests: schema defaults, validation, backward compat with existing flows

### Task 2 — Template engine (`core/flows/template.py`)
- `_render(template, collected)` — `{{ collected.x }}` interpolation
- Handles nested keys: `{{ collected.customer.name }}`
- Returns empty string (not error) for missing keys
- Tests: simple, nested, missing key, non-string value, no template in string

### Task 3 — Expression evaluator (`core/flows/expr.py`)
- `_eval_expr(expr, collected)` — safe restricted grammar
- Operators: `==`, `!=`, `in`, `>`, `<`, `>=`, `<=`, `starts_with`, `is_empty`, `is_not_empty`
- No `eval()` — custom parser over limited token set
- Tests: each operator, nested field, missing field, malformed expr (returns False + logs)

### Task 4 — Engine: cancel_if + step timeout
- Add cancel_if check at top of `resolve()` before all other logic
- Add step timeout check using `state.step_started_at` vs `default_step_timeout_minutes`
- Tests: cancel fires on keyword match, timeout re-asks, timeout escalates

### Task 5 — Engine: validation loop
- Add validation to message step processing
- Retry loop: increment `state.retry_counts[step_id]`, re-send error_message
- On exceed: escalate / cancel / send_message
- Tests: valid answer passes, invalid loops, max retries escalates

### Task 6 — Engine: lookup steps
- `_execute_lookup()` — template URL+payload, call webhook, retry with backoff, extract response_key
- Sensitive field redaction in logs
- on_error routing
- Tests: successful lookup advances, HTTP error jumps to error_jump, timeout handled

### Task 7 — Engine: branch + set steps
- `_evaluate_branch()` — iterate conditions, call `_eval_expr`, return jump_to
- `_execute_set()` — evaluate simple arithmetic/string expression, store result
- Tests: first condition matches, else clause, no match (logs + completes), set stores value

### Task 8 — Engine: templating integration
- Apply `_render()` to all string fields before use: step.message, webhook URLs, webhook payload values, cancel_message, timeout_message
- Tests: message contains template, webhook payload contains template

### Task 9 — Webhook retry (outbound — on_complete + lookup)
- Implement `_call_webhook_with_retry(url, method, payload, attempts, backoff)` in `core/flows/engine.py`
- Exponential backoff: `backoff * (2 ** attempt)` with jitter
- Log each retry attempt
- Dead-letter: on final failure log full payload at ERROR level
- Tests: first attempt succeeds, second attempt succeeds, all fail → error log

### Task 10 — Docs + example config update
- Update `clients/example/config.yaml` with annotated v2 flow examples
- Update `CLAUDE.md` Completed Phases + Key Modules
- Update `README.md` flows YAML reference

---

## Backward compatibility

All new fields are optional with defaults. Every existing `config.yaml` that uses Phase 8 flows continues to work without modification. The engine's default code path (no `type` field = `type: message`, no `validate`, no `skip_if`) is identical to the current behavior.

---

## Verification checklist

```bash
# 1. Existing flow tests still pass
pytest tests/framework/test_flows.py -v

# 2. New flow engine v2 tests
pytest tests/framework/test_flow_engine_v2.py -v

# 3. Full suite
pytest tests/ -q

# 4. Schema smoke test
PYTHONPATH=. python -c "
from core.registry.schema import FrameworkConfig, StepType
c = FrameworkConfig(client_id='test', agent={'name':'Bot','model':'gpt-4o-mini'})
print('StepType.lookup:', StepType.lookup)
print('OK')
"

# 5. Lint
ruff check core/flows/ tests/framework/test_flow_engine_v2.py
```
