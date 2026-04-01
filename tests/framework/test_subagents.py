"""
Tests for Phase 10 D1+D2: parallel_tool_calls and sub-agent wiring.

Strategy:
- Schema tests: validate new fields parse correctly
- runner tests: mock SDK to verify Agent/ModelSettings constructed correctly
- Sub-agent as_tool: verify sub-agents are appended to tools list
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_smoke_config():
    """Load smoke-test config as FrameworkConfig."""
    from core.registry.schema import FrameworkConfig
    config_path = Path(__file__).resolve().parents[2] / "clients" / "smoke-test" / "config.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return FrameworkConfig(**raw)


# ---------------------------------------------------------------------------
# D1 — parallel_tool_calls schema field
# ---------------------------------------------------------------------------

def test_parallel_tool_calls_default_true():
    """parallel_tool_calls defaults to True."""
    from core.registry.schema import AgentConfig
    cfg = AgentConfig(name="test", model="openai/gpt-4.1-mini")
    assert cfg.parallel_tool_calls is True


def test_parallel_tool_calls_can_be_disabled():
    """parallel_tool_calls can be set to False in config."""
    from core.registry.schema import AgentConfig
    cfg = AgentConfig(name="test", model="openai/gpt-4.1-mini", parallel_tool_calls=False)
    assert cfg.parallel_tool_calls is False


def test_parallel_tool_calls_in_model_settings():
    """create_model_settings passes parallel_tool_calls to ModelSettings."""
    from agents import ModelSettings

    from core.llm.bifrost import create_model_settings

    config = _load_smoke_config()

    # Default (True)
    settings = create_model_settings(config)
    assert isinstance(settings, ModelSettings)
    assert settings.parallel_tool_calls is True

    # Disabled
    config.agent.parallel_tool_calls = False
    settings_off = create_model_settings(config)
    assert settings_off.parallel_tool_calls is False


# ---------------------------------------------------------------------------
# D1 — max_turns in Runner.run()
# ---------------------------------------------------------------------------

def test_max_turns_passed_to_runner(monkeypatch):
    """Runner.run() receives max_turns from sdk.handoffs.max_turns."""
    import asyncio

    from core.engine.runner import run_agent

    captured = {}

    async def mock_runner_run(agent, input, max_turns=10):
        captured["max_turns"] = max_turns
        mock_result = MagicMock()
        mock_result.final_output = "ok"
        mock_result.new_items = []
        return mock_result

    with patch("core.engine.runner.Runner.run", side_effect=mock_runner_run):
        with patch("core.engine.runner.create_primary_model", return_value=MagicMock()):
            registry = MagicMock()
            registry.agent_name = "test"
            registry.system_prompt = "You are a test agent."
            registry.tools = []
            config = _load_smoke_config()
            config.sdk.handoffs.max_turns = 7
            registry.config = config

            asyncio.run(run_agent(registry, "hello"))

    assert captured.get("max_turns") == 7


# ---------------------------------------------------------------------------
# D2 — SubAgentConfig schema
# ---------------------------------------------------------------------------

def test_subagent_config_required_fields():
    """SubAgentConfig requires name, tool_name, tool_description."""
    from core.registry.schema import SubAgentConfig
    sub = SubAgentConfig(
        name="logistics",
        tool_name="consultar_entrega",
        tool_description="Consulta prazo de entrega",
        instructions="Você é especialista em logística.",
    )
    assert sub.name == "logistics"
    assert sub.tool_name == "consultar_entrega"
    assert sub.max_turns == 5  # default


def test_subagent_config_defaults():
    """SubAgentConfig has sensible defaults."""
    from core.registry.schema import SubAgentConfig
    sub = SubAgentConfig(
        name="test",
        tool_name="test_tool",
        tool_description="A test tool",
    )
    assert sub.instructions == ""
    assert sub.ref == ""
    assert sub.model == ""
    assert sub.tools == []
    assert sub.max_turns == 5


def test_subagents_field_on_framework_config():
    """FrameworkConfig accepts subagents list."""
    from core.registry.schema import SubAgentConfig

    config = _load_smoke_config()
    assert config.subagents == []  # default empty

    config.subagents = [
        SubAgentConfig(
            name="specialist",
            tool_name="ask_specialist",
            tool_description="Ask the specialist agent",
            instructions="You are a specialist.",
        )
    ]
    assert len(config.subagents) == 1


# ---------------------------------------------------------------------------
# D2 — Sub-agent added to agent tools in build_agent()
# ---------------------------------------------------------------------------

def test_build_agent_adds_subagent_as_tool():
    """build_agent() converts SubAgentConfig into as_tool() entries."""
    from agents import ModelSettings

    from core.engine.runner import build_agent
    from core.registry.schema import SubAgentConfig

    mock_registry = MagicMock()
    mock_registry.agent_name = "orchestrator"
    mock_registry.system_prompt = "You are an orchestrator."
    mock_registry.tools = []

    config = _load_smoke_config()
    config.subagents = [
        SubAgentConfig(
            name="specialist",
            tool_name="ask_specialist",
            tool_description="Invoke the specialist",
            instructions="You are a specialist.",
        )
    ]
    mock_registry.config = config

    # Use a valid string model so Agent() validation passes
    model = "gpt-4o-mini"
    settings = ModelSettings(temperature=0.7, max_tokens=512)

    agent = build_agent(mock_registry, model, settings)

    # Agent should have 1 tool (the sub-agent converted via as_tool)
    assert len(agent.tools) == 1


def test_build_agent_no_subagents():
    """build_agent() with no subagents still works — tools come from registry only."""
    from agents import ModelSettings

    from core.engine.runner import build_agent

    mock_registry = MagicMock()
    mock_registry.agent_name = "simple"
    mock_registry.system_prompt = "Simple agent."
    mock_registry.tools = []

    config = _load_smoke_config()
    config.subagents = []
    mock_registry.config = config

    agent = build_agent(mock_registry, "gpt-4o-mini", ModelSettings(temperature=0.7, max_tokens=512))
    assert len(agent.tools) == 0


# ---------------------------------------------------------------------------
# D2 — SubAgentConfig with tools parses correctly
# ---------------------------------------------------------------------------

def test_subagent_with_inline_tools():
    """SubAgentConfig can include webhook tools inline."""
    from core.registry.schema import SubAgentConfig, ToolRef, ToolType, WebhookParam

    sub = SubAgentConfig(
        name="catalog",
        tool_name="buscar_produto",
        tool_description="Busca produto no catálogo",
        instructions="Você busca produtos.",
        tools=[
            ToolRef(
                name="catalog_api",
                type=ToolType.WEBHOOK,
                webhook_url="http://api.example.com/catalog",
                parameters={
                    "query": WebhookParam(type="string", description="Produto", required=True)
                },
            )
        ],
    )
    assert len(sub.tools) == 1
    assert sub.tools[0].name == "catalog_api"
