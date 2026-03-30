"""
Tests: Phase 8 — Flows (State Machine)

All tests use AsyncMock for Redis — no real Redis required.
asyncio_mode = "auto" (from pyproject.toml) — all async def tests run natively.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from core.flows.engine import FlowEngine
from core.flows.state import FlowState
from core.registry.schema import (
    FlowDefinition,
    FlowsConfig,
    FrameworkConfig,
    OnCompleteAction,
    OnCompleteConfig,
    OnInterruptAction,
    StepConfig,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_two_step_flow(
    flow_id: str = "onboarding",
    on_interrupt: OnInterruptAction = OnInterruptAction.PAUSE,
) -> FlowDefinition:
    return FlowDefinition(
        id=flow_id,
        trigger_keywords=["quero contratar", "começar"],
        steps=[
            StepConfig(id="ask_name", message="Qual é o seu nome?", collect_as="name", next="ask_email"),
            StepConfig(
                id="ask_email",
                message="Qual é o seu email?",
                collect_as="email",
                on_complete=OnCompleteConfig(action=OnCompleteAction.SEND_MESSAGE, message="Obrigado!"),
            ),
        ],
        on_interrupt=on_interrupt,
    )


def _make_engine(*flows: FlowDefinition) -> FlowEngine:
    return FlowEngine(FlowsConfig(enabled=True, flows=list(flows)))


def _mock_redis(state: FlowState | None = None) -> AsyncMock:
    """Redis session mock. get_flow_state returns given state."""
    redis = AsyncMock()
    redis.get_flow_state = AsyncMock(return_value=state)
    redis.set_flow_state = AsyncMock()
    redis.clear_flow_state = AsyncMock()
    return redis


# ---------------------------------------------------------------------------
# Schema tests
# ---------------------------------------------------------------------------

def test_flows_default_off():
    """FlowsConfig is DEFAULT OFF."""
    config = FrameworkConfig(client_id="test", agent={"name": "Bot"})
    assert config.flows.enabled is False
    assert config.flows.flows == []
    assert config.flows.default_state_ttl == 1800


def test_flow_schema_valid():
    """FlowsConfig parses a complete flows YAML block."""
    raw = {
        "client_id": "test",
        "agent": {"name": "Bot"},
        "flows": {
            "enabled": True,
            "default_state_ttl": 900,
            "flows": [
                {
                    "id": "onboarding",
                    "trigger_keywords": ["começar"],
                    "on_interrupt": "hold",
                    "steps": [
                        {"id": "ask_name", "message": "Seu nome?", "collect_as": "name"},
                    ],
                }
            ],
        },
    }
    config = FrameworkConfig(**raw)
    assert config.flows.enabled is True
    assert config.flows.default_state_ttl == 900
    assert len(config.flows.flows) == 1
    assert config.flows.flows[0].id == "onboarding"
    assert config.flows.flows[0].on_interrupt == OnInterruptAction.HOLD
    assert config.flows.flows[0].steps[0].collect_as == "name"


def test_flow_invalid_regex():
    """Bad trigger_regex raises ValidationError."""
    with pytest.raises(ValidationError, match="regex"):
        FlowDefinition(
            id="bad",
            trigger_regex=["[invalid("],
            steps=[StepConfig(id="s1", message="Hi")],
        )


# ---------------------------------------------------------------------------
# FlowState serialization
# ---------------------------------------------------------------------------

def test_flow_state_serialization():
    """FlowState round-trips correctly through to_json/from_json."""
    state = FlowState(
        flow_id="onboarding",
        step_id="ask_email",
        collected={"name": "João"},
        started_at=1000.0,
        step_started_at=1005.0,
    )
    restored = FlowState.from_json(state.to_json())
    assert restored.flow_id == "onboarding"
    assert restored.step_id == "ask_email"
    assert restored.collected == {"name": "João"}
    assert restored.started_at == 1000.0
    assert restored.step_started_at == 1005.0


# ---------------------------------------------------------------------------
# Engine: no active flow
# ---------------------------------------------------------------------------

async def test_engine_no_trigger_no_state():
    """No active flow + no trigger match → action='none'."""
    engine = _make_engine(_make_two_step_flow())
    redis = _mock_redis(state=None)
    result = await engine.resolve("5511999999999", "olá tudo bem", redis)
    assert result.action == "none"
    redis.set_flow_state.assert_not_called()


async def test_engine_trigger_starts_flow():
    """Trigger keyword → action='start', state saved, first step message returned."""
    engine = _make_engine(_make_two_step_flow())
    redis = _mock_redis(state=None)

    result = await engine.resolve("5511999999999", "quero contratar um plano", redis)

    assert result.action == "start"
    assert result.message == "Qual é o seu nome?"
    redis.set_flow_state.assert_called_once()
    saved_state: FlowState = redis.set_flow_state.call_args[0][1]
    assert saved_state.flow_id == "onboarding"
    assert saved_state.step_id == "ask_name"
    assert saved_state.collected == {}


# ---------------------------------------------------------------------------
# Engine: active flow — normal progression
# ---------------------------------------------------------------------------

async def test_engine_advance_step():
    """Answer to step 1 → state advances to step 2, returns step 2 message."""
    active_state = FlowState(flow_id="onboarding", step_id="ask_name")
    engine = _make_engine(_make_two_step_flow())
    redis = _mock_redis(state=active_state)

    result = await engine.resolve("5511999999999", "João Silva", redis)

    assert result.action == "advance"
    assert result.message == "Qual é o seu email?"
    redis.set_flow_state.assert_called_once()
    saved_state: FlowState = redis.set_flow_state.call_args[0][1]
    assert saved_state.step_id == "ask_email"


async def test_collect_as_stores_data():
    """User's reply is stored under the collect_as key."""
    active_state = FlowState(flow_id="onboarding", step_id="ask_name")
    engine = _make_engine(_make_two_step_flow())
    redis = _mock_redis(state=active_state)

    await engine.resolve("5511999999999", "Maria", redis)

    saved_state: FlowState = redis.set_flow_state.call_args[0][1]
    assert saved_state.collected["name"] == "Maria"


async def test_engine_last_step_complete():
    """Answer to last step → action='complete', state cleared, on_complete set."""
    active_state = FlowState(
        flow_id="onboarding",
        step_id="ask_email",
        collected={"name": "João"},
    )
    engine = _make_engine(_make_two_step_flow())
    redis = _mock_redis(state=active_state)

    result = await engine.resolve("5511999999999", "joao@example.com", redis)

    assert result.action == "complete"
    assert result.collected["name"] == "João"
    assert result.collected["email"] == "joao@example.com"
    assert result.on_complete is not None
    assert result.on_complete.action == OnCompleteAction.SEND_MESSAGE
    redis.clear_flow_state.assert_called_once_with("5511999999999")


# ---------------------------------------------------------------------------
# Engine: on_interrupt
# ---------------------------------------------------------------------------

async def test_engine_on_interrupt_hold():
    """Off-topic message (matches another flow) with hold → re-ask step, no advance."""
    checkout_flow = FlowDefinition(
        id="checkout",
        trigger_keywords=["finalizar pedido"],
        steps=[StepConfig(id="s1", message="Confirme o endereço:")],
    )
    onboarding = _make_two_step_flow(on_interrupt=OnInterruptAction.HOLD)
    active_state = FlowState(flow_id="onboarding", step_id="ask_name")
    engine = _make_engine(onboarding, checkout_flow)
    redis = _mock_redis(state=active_state)

    result = await engine.resolve("5511999999999", "finalizar pedido", redis)

    assert result.action == "hold"
    assert result.message == "Qual é o seu nome?"  # re-ask current step
    redis.set_flow_state.assert_not_called()
    redis.clear_flow_state.assert_not_called()


async def test_engine_on_interrupt_pause():
    """Off-topic message with pause → action='pause', step_message set for re-ask after LLM."""
    checkout_flow = FlowDefinition(
        id="checkout",
        trigger_keywords=["finalizar pedido"],
        steps=[StepConfig(id="s1", message="Confirme o endereço:")],
    )
    onboarding = _make_two_step_flow(on_interrupt=OnInterruptAction.PAUSE)
    active_state = FlowState(flow_id="onboarding", step_id="ask_name")
    engine = _make_engine(onboarding, checkout_flow)
    redis = _mock_redis(state=active_state)

    result = await engine.resolve("5511999999999", "finalizar pedido", redis)

    assert result.action == "pause"
    assert result.step_message == "Qual é o seu nome?"
    redis.set_flow_state.assert_not_called()


# ---------------------------------------------------------------------------
# Engine: TTL
# ---------------------------------------------------------------------------

async def test_engine_ttl_override():
    """Flow with state_ttl=600 uses 600, not default_state_ttl=1800."""
    flow = FlowDefinition(
        id="fast",
        trigger_keywords=["rápido"],
        state_ttl=600,
        steps=[StepConfig(id="s1", message="Pergunta?")],
    )
    engine = FlowEngine(FlowsConfig(enabled=True, default_state_ttl=1800, flows=[flow]))
    redis = _mock_redis(state=None)

    await engine.resolve("5511999999999", "rápido", redis)

    ttl = redis.set_flow_state.call_args.args[2]
    assert ttl == 600


async def test_engine_default_ttl_used_when_no_override():
    """Flow without state_ttl override uses default_state_ttl."""
    flow = FlowDefinition(
        id="slow",
        trigger_keywords=["devagar"],
        state_ttl=0,
        steps=[StepConfig(id="s1", message="Pergunta?")],
    )
    engine = FlowEngine(FlowsConfig(enabled=True, default_state_ttl=3600, flows=[flow]))
    redis = _mock_redis(state=None)

    await engine.resolve("5511999999999", "devagar", redis)

    ttl = redis.set_flow_state.call_args.args[2]
    assert ttl == 3600
