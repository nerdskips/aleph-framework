"""
Tests for core/mcp/server.py

Strategy: call the underlying tool functions directly (no MCP stdio protocol).
- Read-only tests  → use the real clients/smoke-test agent
- Write tests      → use pytest's tmp_path, monkeypatch CLIENTS_DIR
- chat_message     → mock process_message at the pipeline level
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

SMOKE_TEST = "smoke-test"


@pytest.fixture()
def mock_clients_dir(tmp_path, monkeypatch):
    """Point ALEPH_CLIENTS_DIR to a temp directory for write tests."""
    monkeypatch.setenv("ALEPH_CLIENTS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture()
def smoke_agent_in_tmp(tmp_path, monkeypatch):
    """
    Copy smoke-test into tmp_path so write tests don't touch the real agent.
    ALEPH_CLIENTS_DIR is set to tmp_path.
    """
    import shutil

    real_smoke = Path(__file__).resolve().parents[2] / "clients" / "smoke-test"
    dest = tmp_path / "smoke-test"
    shutil.copytree(real_smoke, dest)
    monkeypatch.setenv("ALEPH_CLIENTS_DIR", str(tmp_path))
    return tmp_path


# ---------------------------------------------------------------------------
# list_agents
# ---------------------------------------------------------------------------

def test_list_agents_returns_smoke_test():
    from core.mcp.server import list_agents
    result = list_agents()
    agents = json.loads(result)
    names = [a["name"] for a in agents]
    assert SMOKE_TEST in names


def test_list_agents_json_parseable():
    from core.mcp.server import list_agents
    result = list_agents()
    parsed = json.loads(result)
    assert isinstance(parsed, list)


def test_list_agents_has_expected_fields():
    from core.mcp.server import list_agents
    result = list_agents()
    agents = json.loads(result)
    smoke = next(a for a in agents if a["name"] == SMOKE_TEST)
    assert "model" in smoke
    assert "flows_enabled" in smoke
    assert "running" in smoke
    assert "path" in smoke
    assert isinstance(smoke["running"], bool)
    assert "smoke-test" in smoke["path"]


def test_list_agents_empty_dir(mock_clients_dir):
    from core.mcp.server import list_agents
    result = list_agents()
    assert json.loads(result) == []


# ---------------------------------------------------------------------------
# create_agent
# ---------------------------------------------------------------------------

def test_create_agent_creates_files(mock_clients_dir):
    from core.mcp.server import create_agent
    result = create_agent("test-bot")
    assert "created" in result.lower()

    agent_dir = mock_clients_dir / "test-bot"
    assert (agent_dir / "config.yaml").is_file()
    assert (agent_dir / ".env").is_file()
    assert (agent_dir / "prompts" / "system.md").is_file()
    assert (agent_dir / "Dockerfile").is_file()
    assert (agent_dir / "tools").is_dir()
    assert (agent_dir / "data").is_dir()


def test_create_agent_substitutes_name(mock_clients_dir):
    from core.mcp.server import create_agent
    create_agent("minha-loja")
    config = yaml.safe_load((mock_clients_dir / "minha-loja" / "config.yaml").read_text())
    assert config["client_id"] == "minha-loja"


def test_create_agent_substitutes_model(mock_clients_dir):
    from core.mcp.server import create_agent
    create_agent("bot-a", model="openai/gpt-4.1")
    config_text = (mock_clients_dir / "bot-a" / "config.yaml").read_text()
    assert "gpt-4.1" in config_text


def test_create_agent_duplicate_returns_error(mock_clients_dir):
    from core.mcp.server import create_agent
    create_agent("dup-bot")
    result = create_agent("dup-bot")
    assert result.startswith("Error")


# ---------------------------------------------------------------------------
# validate_agent
# ---------------------------------------------------------------------------

def test_validate_agent_passes_smoke_test():
    from core.mcp.server import validate_agent
    result = validate_agent(SMOKE_TEST)
    assert result.startswith("PASSED")


def test_validate_agent_missing_agent():
    from core.mcp.server import validate_agent
    result = validate_agent("ghost-agent-xyz")
    assert result.startswith("FAILED")
    assert "not found" in result.lower()


def test_validate_agent_broken_yaml(mock_clients_dir):
    from core.mcp.server import validate_agent
    agent_dir = mock_clients_dir / "broken"
    agent_dir.mkdir()
    (agent_dir / "config.yaml").write_text("this: is: broken: yaml: [[[")
    result = validate_agent("broken")
    assert result.startswith("FAILED")


def test_validate_agent_missing_prompt(mock_clients_dir):
    from core.mcp.server import create_agent, validate_agent
    create_agent("no-prompt")
    # Remove the system prompt
    (mock_clients_dir / "no-prompt" / "prompts" / "system.md").unlink()
    result = validate_agent("no-prompt")
    assert result.startswith("FAILED")


# ---------------------------------------------------------------------------
# get_config
# ---------------------------------------------------------------------------

def test_get_config_returns_yaml_text():
    from core.mcp.server import get_config
    result = get_config(SMOKE_TEST)
    assert "client_id" in result
    assert "smoke-test" in result


def test_get_config_missing_agent():
    from core.mcp.server import get_config
    result = get_config("ghost-xyz")
    assert result.startswith("Error")


# ---------------------------------------------------------------------------
# update_config
# ---------------------------------------------------------------------------

def test_update_config_roundtrip(smoke_agent_in_tmp):
    from core.mcp.server import get_config, update_config
    original = get_config(SMOKE_TEST)
    result = update_config(SMOKE_TEST, original)
    assert "updated" in result.lower()
    assert get_config(SMOKE_TEST) == original


def test_update_config_validates_before_write(smoke_agent_in_tmp):
    from core.mcp.server import get_config, update_config
    original = get_config(SMOKE_TEST)
    bad_yaml = "this: is: [broken"
    result = update_config(SMOKE_TEST, bad_yaml)
    assert result.startswith("Error")
    # File must be unchanged
    assert get_config(SMOKE_TEST) == original


def test_update_config_rejects_missing_required_field(smoke_agent_in_tmp):
    from core.mcp.server import update_config
    # Missing client_id — Pydantic should reject
    bad_config = "agent:\n  name: Test\n  model: openai/gpt-4.1-mini\n"
    result = update_config(SMOKE_TEST, bad_config)
    assert result.startswith("Error")
    assert "NOT saved" in result


# ---------------------------------------------------------------------------
# get_system_prompt
# ---------------------------------------------------------------------------

def test_get_system_prompt_returns_content():
    from core.mcp.server import get_system_prompt
    result = get_system_prompt(SMOKE_TEST)
    assert len(result) > 20
    assert not result.startswith("Error")


def test_get_system_prompt_missing_agent():
    from core.mcp.server import get_system_prompt
    result = get_system_prompt("ghost-xyz")
    assert result.startswith("Error")


# ---------------------------------------------------------------------------
# update_system_prompt
# ---------------------------------------------------------------------------

def test_update_system_prompt_roundtrip(smoke_agent_in_tmp):
    from core.mcp.server import get_system_prompt, update_system_prompt
    new_content = "# Test Agent\nYou are a helpful assistant."
    result = update_system_prompt(SMOKE_TEST, new_content)
    assert "updated" in result.lower()
    assert get_system_prompt(SMOKE_TEST) == new_content


# ---------------------------------------------------------------------------
# chat_message
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_chat_message_returns_string():
    """chat_message should return the pipeline response string."""
    from core.mcp.server import chat_message

    mock_result = MagicMock()
    mock_result.response = "Olá! Como posso ajudar?"

    with (
        patch("core.engine.pipeline.process_message", new=AsyncMock(return_value=mock_result)),
        patch("core.session.redis.RedisSession.connect", new=AsyncMock()),
        patch("core.session.redis.RedisSession.close", new=AsyncMock()),
    ):
        result = await chat_message(SMOKE_TEST, "oi")

    assert isinstance(result, str)
    assert "Olá" in result


@pytest.mark.asyncio
async def test_chat_message_missing_agent():
    """chat_message returns error string for unknown agent — does not raise."""
    from core.mcp.server import chat_message
    result = await chat_message("ghost-xyz", "hi")
    assert isinstance(result, str)
    assert result.startswith("Error")


# ---------------------------------------------------------------------------
# _agent_dir resolution
# ---------------------------------------------------------------------------

def test_agent_dir_env_var(tmp_path, monkeypatch):
    """ALEPH_CLIENTS_DIR overrides all other resolution."""
    from core.mcp.server import _agent_dir
    monkeypatch.setenv("ALEPH_CLIENTS_DIR", str(tmp_path))
    result = _agent_dir("mybot")
    assert result == tmp_path / "mybot"


def test_agent_dir_direct_cwd(tmp_path, monkeypatch):
    """cwd/<name> is returned when the directory exists and env var is not set."""
    from core.mcp.server import _agent_dir
    monkeypatch.delenv("ALEPH_CLIENTS_DIR", raising=False)
    agent_dir = tmp_path / "mybot"
    agent_dir.mkdir()
    monkeypatch.chdir(tmp_path)
    result = _agent_dir("mybot")
    assert result == agent_dir


def test_agent_dir_via_clients_subdir(tmp_path, monkeypatch):
    """cwd/clients/<name> is returned when cwd/<name> does not exist."""
    from core.mcp.server import _agent_dir
    monkeypatch.delenv("ALEPH_CLIENTS_DIR", raising=False)
    agent_dir = tmp_path / "clients" / "mybot"
    agent_dir.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    result = _agent_dir("mybot")
    assert result == agent_dir


def test_agent_dir_walk_up(tmp_path, monkeypatch):
    """Walk-up finds clients/<name> in a parent directory."""
    from core.mcp.server import _agent_dir
    monkeypatch.delenv("ALEPH_CLIENTS_DIR", raising=False)
    agent_dir = tmp_path / "clients" / "mybot"
    agent_dir.mkdir(parents=True)
    subdir = tmp_path / "deep" / "sub"
    subdir.mkdir(parents=True)
    monkeypatch.chdir(subdir)
    result = _agent_dir("mybot")
    assert result == agent_dir


def test_agent_dir_fallback(tmp_path, monkeypatch):
    """Returns cwd/clients/<name> as fallback when nothing is found."""
    from core.mcp.server import _agent_dir
    monkeypatch.delenv("ALEPH_CLIENTS_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    result = _agent_dir("nonexistent")
    assert result == tmp_path / "clients" / "nonexistent"
