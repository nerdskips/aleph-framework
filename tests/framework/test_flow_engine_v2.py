"""
Tests: Phase 15 — Flow Engine v2 (conditional, validated, dynamic flows)

All tests use AsyncMock for Redis — no real Redis or HTTP required.
asyncio_mode = "auto" (from pyproject.toml) — all async def tests run natively.

Coverage:
  - Template engine (render)
  - Expression evaluator (evaluate)
  - cancel_if
  - Step timeout (re_ask, cancel, escalate)
  - Tool-based validation (fail, retry, exceed)
  - Lookup steps (success, error routing, retry, response_key)
  - Branch steps (first match, else clause, no match)
  - skip_if
  - Sensitive field redaction
  - Non-message step auto-advance chains (lookup → branch → message)
  - Backward compatibility — plain Phase 8 flows unchanged
"""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, patch

from core.flows.engine import FlowEngine
from core.flows.expr import evaluate
from core.flows.state import FlowState
from core.flows.template import render, render_dict
from core.registry.schema import (
    BranchCondition,
    FlowDefinition,
    FlowsConfig,
    LookupConfig,
    StepConfig,
    StepType,
    StepValidation,
    ToolRef,
    ToolType,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_engine(*flows: FlowDefinition, tools=None) -> FlowEngine:
    return FlowEngine(FlowsConfig(enabled=True, flows=list(flows)), tools=tools or [])


def _mock_redis(state: FlowState | None = None) -> AsyncMock:
    redis = AsyncMock()
    redis.get_flow_state = AsyncMock(return_value=state)
    redis.set_flow_state = AsyncMock()
    redis.clear_flow_state = AsyncMock()
    return redis


def _state(flow_id: str, step_id: str, collected: dict | None = None, retry_counts: dict | None = None) -> FlowState:
    s = FlowState(flow_id=flow_id, step_id=step_id, collected=collected or {})
    s.retry_counts = retry_counts or {}
    return s


# ===========================================================================
# Template engine
# ===========================================================================

class TestTemplate:
    def test_simple_replacement(self):
        assert render("Hello {{ collected.name }}!", {"name": "Alice"}) == "Hello Alice!"

    def test_collected_prefix_optional(self):
        assert render("Hello {{ name }}!", {"name": "Bob"}) == "Hello Bob!"

    def test_nested_key(self):
        assert render("{{ customer.type }}", {"customer": {"type": "A"}}) == "A"

    def test_missing_key_returns_empty(self):
        assert render("{{ collected.missing }}", {}) == ""

    def test_no_template_passthrough(self):
        assert render("No placeholders here", {"x": "y"}) == "No placeholders here"

    def test_multiple_placeholders(self):
        result = render("{{ name }}: {{ value }}", {"name": "CPF", "value": "123"})
        assert result == "CPF: 123"

    def test_non_string_value(self):
        assert render("Score: {{ score }}", {"score": 42}) == "Score: 42"

    def test_render_dict_replaces_values(self):
        result = render_dict({"cpf": "{{ collected.cpf }}", "static": "ok"}, {"cpf": "111"})
        assert result == {"cpf": "111", "static": "ok"}


# ===========================================================================
# Expression evaluator
# ===========================================================================

class TestEvaluate:
    def test_eq_match(self):
        assert evaluate("collected.type == 'A'", {"type": "A"}) is True

    def test_eq_no_match(self):
        assert evaluate("collected.type == 'B'", {"type": "A"}) is False

    def test_ne(self):
        assert evaluate("type != 'B'", {"type": "A"}) is True

    def test_in_list(self):
        assert evaluate("type in ['A', 'B']", {"type": "A"}) is True

    def test_in_list_no_match(self):
        assert evaluate("type in ['X', 'Y']", {"type": "A"}) is False

    def test_gt(self):
        assert evaluate("collected.age > 18", {"age": "25"}) is True

    def test_lt(self):
        assert evaluate("age < 10", {"age": "5"}) is True

    def test_gte(self):
        assert evaluate("age >= 18", {"age": "18"}) is True

    def test_lte(self):
        assert evaluate("age <= 17", {"age": "17"}) is True

    def test_starts_with(self):
        assert evaluate("collected.answer starts_with 'sim'", {"answer": "sim, claro"}) is True

    def test_ends_with(self):
        assert evaluate("answer ends_with 'ok'", {"answer": "está ok"}) is True

    def test_is_empty(self):
        assert evaluate("collected.missing is_empty", {}) is True

    def test_is_not_empty(self):
        assert evaluate("collected.cpf is_not_empty", {"cpf": "123"}) is True

    def test_nested_path(self):
        assert evaluate("customer.type == 'B'", {"customer": {"type": "B"}}) is True

    def test_malformed_returns_false(self):
        assert evaluate("this is gibberish ??", {}) is False

    def test_empty_expr_returns_false(self):
        assert evaluate("", {}) is False


# ===========================================================================
# cancel_if
# ===========================================================================

class TestCancelIf:
    def _make_flow(self) -> FlowDefinition:
        return FlowDefinition(
            id="test",
            trigger_keywords=["inicio"],
            cancel_if=["cancelar", "desistir"],
            on_cancel="send_message",
            cancel_message="Fluxo cancelado!",
            steps=[StepConfig(id="s1", message="Pergunta?", collect_as="resp")],
        )

    async def test_cancel_if_fires(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        redis = _mock_redis(state=_state("test", "s1"))

        result = await engine.resolve("555", "quero cancelar isso", redis)

        assert result.action == "cancelled"
        assert "cancelado" in result.message
        redis.clear_flow_state.assert_called_once()

    async def test_cancel_if_not_triggered_on_normal_message(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        redis = _mock_redis(state=_state("test", "s1"))

        result = await engine.resolve("555", "minha resposta", redis)

        assert result.action == "complete"  # last step


# ===========================================================================
# Step timeout
# ===========================================================================

class TestStepTimeout:
    def _make_flow(self, action: str) -> FlowDefinition:
        return FlowDefinition(
            id="test",
            trigger_keywords=["start"],
            default_step_timeout_minutes=0.001,  # ~0.06s — always exceeded in tests
            default_timeout_action=action,
            default_timeout_message="Ainda por aqui?",
            cancel_message="Tempo esgotado.",
            steps=[StepConfig(id="s1", message="Pergunta?", collect_as="resp")],
        )

    async def test_timeout_re_ask(self):
        flow = self._make_flow("re_ask")
        engine = _make_engine(flow)
        state = _state("test", "s1")
        state.step_started_at = time.time() - 10  # expired
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "resposta", redis)

        assert result.action == "timeout_reask"
        assert result.message == "Ainda por aqui?"

    async def test_timeout_re_ask_uses_step_message_when_no_default_timeout_message(self):
        flow = FlowDefinition(
            id="test",
            trigger_keywords=["start"],
            default_step_timeout_minutes=0.001,
            default_timeout_action="re_ask",
            default_timeout_message="",  # empty → use step message
            steps=[StepConfig(id="s1", message="Original question?", collect_as="resp")],
        )
        engine = _make_engine(flow)
        state = _state("test", "s1")
        state.step_started_at = time.time() - 10
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "x", redis)

        assert result.message == "Original question?"

    async def test_timeout_cancel(self):
        flow = self._make_flow("cancel")
        engine = _make_engine(flow)
        state = _state("test", "s1")
        state.step_started_at = time.time() - 10
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "resposta", redis)

        assert result.action == "timeout_cancelled"
        redis.clear_flow_state.assert_called_once()

    async def test_timeout_escalate(self):
        flow = self._make_flow("escalate")
        engine = _make_engine(flow)
        state = _state("test", "s1")
        state.step_started_at = time.time() - 10
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "resposta", redis)

        assert result.action == "timeout_escalated"
        redis.clear_flow_state.assert_called_once()


# ===========================================================================
# Tool-based validation
# ===========================================================================

class TestValidation:
    def _make_tool(self, name: str = "validate_cpf") -> ToolRef:
        return ToolRef(name=name, type=ToolType.WEBHOOK, webhook_url="https://validator.test/cpf")

    def _make_flow(self, tool_name: str = "validate_cpf", max_retries: int = 2) -> FlowDefinition:
        return FlowDefinition(
            id="test",
            trigger_keywords=["start"],
            steps=[
                StepConfig(
                    id="ask_cpf",
                    message="Informe seu CPF:",
                    collect_as="cpf",
                    validation=StepValidation(tool=tool_name, max_retries=max_retries, on_exceed="escalate"),
                ),
            ],
        )

    async def test_validation_pass(self):
        flow = self._make_flow()
        engine = _make_engine(flow, tools=[self._make_tool()])
        redis = _mock_redis(state=_state("test", "ask_cpf"))

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value={"valid": True, "message": ""}),
        ):
            result = await engine.resolve("555", "12345678901", redis)

        # Valid answer — last step, so flow completes
        assert result.action == "complete"
        assert result.collected.get("cpf") == "12345678901"

    async def test_validation_fail_first_retry(self):
        flow = self._make_flow()
        engine = _make_engine(flow, tools=[self._make_tool()])
        redis = _mock_redis(state=_state("test", "ask_cpf"))

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value={"valid": False, "message": "CPF inválido."}),
        ):
            result = await engine.resolve("555", "banana", redis)

        assert result.action == "validate_fail"
        assert result.validation_injection == "CPF inválido."
        assert result.message == "Informe seu CPF:"  # re-ask
        # State saved with retry_count = 1
        set_call_args = redis.set_flow_state.call_args[0][1]
        assert set_call_args.retry_counts["ask_cpf"] == 1

    async def test_validation_fail_max_retries_exceeded(self):
        flow = self._make_flow(max_retries=1)
        engine = _make_engine(flow, tools=[self._make_tool()])
        state = _state("test", "ask_cpf", retry_counts={"ask_cpf": 1})
        redis = _mock_redis(state=state)

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value={"valid": False, "message": "Ainda inválido."}),
        ):
            result = await engine.resolve("555", "banana", redis)

        assert result.action == "validate_exceeded"
        assert result.on_exceed == "escalate"
        redis.clear_flow_state.assert_called_once()

    async def test_validation_tool_not_found_treats_as_valid(self):
        flow = self._make_flow(tool_name="nonexistent_tool")
        engine = _make_engine(flow, tools=[])  # no tools registered
        redis = _mock_redis(state=_state("test", "ask_cpf"))

        result = await engine.resolve("555", "anything", redis)

        # Treated as valid (fail-open) → flow completes
        assert result.action == "complete"

    async def test_validation_webhook_error_treats_as_valid(self):
        flow = self._make_flow()
        engine = _make_engine(flow, tools=[self._make_tool()])
        redis = _mock_redis(state=_state("test", "ask_cpf"))

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value=None),  # HTTP failure
        ):
            result = await engine.resolve("555", "anything", redis)

        assert result.action == "complete"  # fail-open


# ===========================================================================
# Lookup steps
# ===========================================================================

class TestLookupStep:
    def _make_flow(self, on_error: str = "escalate", error_jump: str = "") -> FlowDefinition:
        return FlowDefinition(
            id="crm",
            trigger_keywords=["crm"],
            steps=[
                StepConfig(id="ask_cpf", message="CPF:", collect_as="cpf", next="lookup_customer"),
                StepConfig(
                    id="lookup_customer",
                    type=StepType.LOOKUP,
                    collect_as="customer",
                    lookup=LookupConfig(
                        url="https://crm.test/lookup",
                        payload={"cpf": "{{ collected.cpf }}"},
                        response_key="data",
                        on_error=on_error,
                        error_jump=error_jump,
                        retry_attempts=0,
                    ),
                    next="confirm",
                ),
                StepConfig(id="crm_error", message="CRM indisponível.", collect_as=""),
                StepConfig(id="confirm", message="Olá, {{ customer.name }}!", collect_as="ok"),
            ],
        )

    async def test_lookup_success_advances_to_message(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        state = _state("crm", "lookup_customer", {"cpf": "123"})
        redis = _mock_redis(state=state)

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value={"data": {"name": "João"}}),
        ):
            result = await engine.resolve("555", "(not used — lookup step)", redis)

        # Lookup auto-advanced to "confirm" and rendered template
        assert result.action == "advance"
        assert "João" in result.message

    async def test_lookup_response_key_extraction(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        state = _state("crm", "lookup_customer", {"cpf": "123"})
        redis = _mock_redis(state=state)

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value={"data": {"name": "Ana"}}),
        ):
            await engine.resolve("555", "x", redis)

        # State was updated with the extracted value
        saved_state = redis.set_flow_state.call_args[0][1]
        assert saved_state.collected["customer"] == {"name": "Ana"}

    async def test_lookup_error_escalate(self):
        flow = self._make_flow(on_error="escalate")
        engine = _make_engine(flow)
        state = _state("crm", "lookup_customer", {"cpf": "123"})
        redis = _mock_redis(state=state)

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value=None),  # failure
        ):
            result = await engine.resolve("555", "x", redis)

        assert result.action == "complete"
        redis.clear_flow_state.assert_called_once()

    async def test_lookup_error_jump_to(self):
        flow = self._make_flow(on_error="jump_to", error_jump="crm_error")
        engine = _make_engine(flow)
        state = _state("crm", "lookup_customer", {"cpf": "123"})
        redis = _mock_redis(state=state)

        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value=None),
        ):
            result = await engine.resolve("555", "x", redis)

        # Jumped to crm_error step
        assert result.action == "advance"
        assert "CRM indisponível" in result.message

    async def test_lookup_error_cancel(self):
        flow = FlowDefinition(
            id="crm",
            trigger_keywords=["crm"],
            cancel_message="Desculpe, tente mais tarde.",
            steps=[
                StepConfig(
                    id="lookup",
                    type=StepType.LOOKUP,
                    lookup=LookupConfig(url="https://test", on_error="cancel", retry_attempts=0),
                ),
            ],
        )
        engine = _make_engine(flow)
        state = _state("crm", "lookup")
        redis = _mock_redis(state=state)

        with patch("core.flows.engine._call_webhook_with_retry", AsyncMock(return_value=None)):
            result = await engine.resolve("555", "x", redis)

        assert result.action == "cancelled"
        assert "tente mais tarde" in result.message


# ===========================================================================
# Branch steps
# ===========================================================================

class TestBranchStep:
    def _make_flow(self) -> FlowDefinition:
        return FlowDefinition(
            id="route",
            trigger_keywords=["route"],
            steps=[
                StepConfig(
                    id="route_by_type",
                    type=StepType.BRANCH,
                    conditions=[
                        BranchCondition(**{"if": "collected.type == 'A'", "jump_to": "msg_a"}),
                        BranchCondition(**{"if": "collected.type == 'B'", "jump_to": "msg_b"}),
                        BranchCondition(**{"else": "msg_unknown"}),
                    ],
                ),
                StepConfig(id="msg_a", message="Bem-vindo, cliente A!"),
                StepConfig(id="msg_b", message="Bem-vindo, cliente B!"),
                StepConfig(id="msg_unknown", message="Tipo desconhecido."),
            ],
        )

    async def test_branch_first_condition_matches(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        state = _state("route", "route_by_type", {"type": "A"})
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "x", redis)

        assert result.action == "advance"
        assert "cliente A" in result.message

    async def test_branch_second_condition_matches(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        state = _state("route", "route_by_type", {"type": "B"})
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "x", redis)

        assert result.action == "advance"
        assert "cliente B" in result.message

    async def test_branch_else_clause(self):
        flow = self._make_flow()
        engine = _make_engine(flow)
        state = _state("route", "route_by_type", {"type": "C"})
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "x", redis)

        assert result.action == "advance"
        assert "desconhecido" in result.message

    async def test_branch_no_match_no_else_completes_flow(self):
        flow = FlowDefinition(
            id="route",
            trigger_keywords=["route"],
            steps=[
                StepConfig(
                    id="b",
                    type=StepType.BRANCH,
                    conditions=[
                        BranchCondition(**{"if": "collected.type == 'A'", "jump_to": "msg_a"}),
                    ],
                ),
                StepConfig(id="msg_a", message="A!"),
            ],
        )
        engine = _make_engine(flow)
        state = _state("route", "b", {"type": "Z"})
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "x", redis)

        assert result.action == "complete"


# ===========================================================================
# skip_if
# ===========================================================================

class TestSkipIf:
    async def test_skip_if_jumps_over_step(self):
        flow = FlowDefinition(
            id="test",
            trigger_keywords=["go"],
            steps=[
                StepConfig(id="ask_type", message="Tipo?", collect_as="type", next="maybe_skip"),
                StepConfig(
                    id="maybe_skip",
                    message="Passo opcional",
                    collect_as="optional",
                    skip_if="collected.type == 'A'",
                    next="final",
                ),
                StepConfig(id="final", message="Concluído!"),
            ],
        )
        engine = _make_engine(flow)
        state = _state("test", "ask_type")
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "A", redis)

        # "maybe_skip" skipped, jumped to "final"
        assert result.action == "advance"
        assert "Concluído" in result.message

    async def test_skip_if_false_proceeds_normally(self):
        flow = FlowDefinition(
            id="test",
            trigger_keywords=["go"],
            steps=[
                StepConfig(id="ask_type", message="Tipo?", collect_as="type", next="maybe_skip"),
                StepConfig(
                    id="maybe_skip",
                    message="Passo obrigatório",
                    collect_as="data",
                    skip_if="collected.type == 'A'",
                ),
            ],
        )
        engine = _make_engine(flow)
        state = _state("test", "ask_type")
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "B", redis)

        # Not skipped
        assert result.action == "advance"
        assert "obrigatório" in result.message


# ===========================================================================
# Sensitive field redaction
# ===========================================================================

class TestSensitiveFields:
    async def test_sensitive_field_redacted_in_collected(self):
        flow = FlowDefinition(
            id="test",
            trigger_keywords=["go"],
            steps=[
                StepConfig(id="ask_cpf", message="CPF?", collect_as="cpf", sensitive=True),
            ],
        )
        engine = _make_engine(flow)
        state = _state("test", "ask_cpf")
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "123456789", redis)

        assert result.action == "complete"
        assert result.collected["cpf"] == "[REDACTED]"

    async def test_non_sensitive_field_not_redacted(self):
        flow = FlowDefinition(
            id="test",
            trigger_keywords=["go"],
            steps=[
                StepConfig(id="ask_name", message="Nome?", collect_as="name", sensitive=False),
            ],
        )
        engine = _make_engine(flow)
        state = _state("test", "ask_name")
        redis = _mock_redis(state=state)

        result = await engine.resolve("555", "João", redis)

        assert result.collected["name"] == "João"


# ===========================================================================
# Auto-advance chain (lookup → branch → message)
# ===========================================================================

class TestAutoAdvanceChain:
    async def test_lookup_then_branch_then_message(self):
        flow = FlowDefinition(
            id="chain",
            trigger_keywords=["chain"],
            steps=[
                StepConfig(id="ask", message="CPF?", collect_as="cpf", next="lookup"),
                StepConfig(
                    id="lookup",
                    type=StepType.LOOKUP,
                    collect_as="data",
                    lookup=LookupConfig(
                        url="https://test", response_key="type", retry_attempts=0
                    ),
                    next="branch",
                ),
                StepConfig(
                    id="branch",
                    type=StepType.BRANCH,
                    conditions=[
                        BranchCondition(**{"if": "collected.data == 'premium'", "jump_to": "msg_premium"}),
                        BranchCondition(**{"else": "msg_basic"}),
                    ],
                ),
                StepConfig(id="msg_premium", message="Cliente premium!"),
                StepConfig(id="msg_basic", message="Cliente básico."),
            ],
        )
        engine = _make_engine(flow)
        state = _state("chain", "ask")
        redis = _mock_redis(state=state)

        # First: answer "ask" step → triggers lookup+branch+message chain
        with patch(
            "core.flows.engine._call_webhook_with_retry",
            AsyncMock(return_value={"type": "premium"}),
        ):
            result = await engine.resolve("555", "12345", redis)

        assert result.action == "advance"
        assert "premium" in result.message


# ===========================================================================
# FlowState serialization with retry_counts
# ===========================================================================

class TestFlowStateV2:
    def test_retry_counts_roundtrip(self):
        state = FlowState(flow_id="f", step_id="s")
        state.retry_counts["s"] = 3
        loaded = FlowState.from_json(state.to_json())
        assert loaded.retry_counts == {"s": 3}

    def test_retry_counts_default_empty(self):
        state = FlowState(flow_id="f", step_id="s")
        assert state.retry_counts == {}

    def test_old_state_without_retry_counts_loads_ok(self):
        """Backward compat: Redis state written before Phase 15 has no retry_counts key."""
        import json
        old_payload = json.dumps({
            "flow_id": "f",
            "step_id": "s",
            "collected": {},
            "started_at": 1000.0,
            "step_started_at": 1001.0,
            # no "retry_counts"
        })
        state = FlowState.from_json(old_payload)
        assert state.retry_counts == {}


# ===========================================================================
# Schema — new fields default correctly
# ===========================================================================

class TestSchemaDefaults:
    def test_step_type_default_is_message(self):
        step = StepConfig(id="s", message="Q?")
        assert step.type == StepType.MESSAGE

    def test_step_validation_none_by_default(self):
        step = StepConfig(id="s", message="Q?")
        assert step.validation is None

    def test_step_sensitive_false_by_default(self):
        step = StepConfig(id="s", message="Q?")
        assert step.sensitive is False

    def test_flow_cancel_if_empty_by_default(self):
        flow = FlowDefinition(id="f", trigger_keywords=["k"], steps=[])
        assert flow.cancel_if == []

    def test_flow_timeout_disabled_by_default(self):
        flow = FlowDefinition(id="f", trigger_keywords=["k"], steps=[])
        assert flow.default_step_timeout_minutes == 0.0
