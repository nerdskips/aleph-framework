"""
Aleph Framework — MCP Server
============================
Exposes framework operations as MCP tools so Claude Code can manage
agents without a terminal. Registered once with:

    claude mcp add --scope user aleph aleph-mcp

Transport: stdio — Claude Code spawns this as a subprocess, no extra
server or Docker needed.

Tools
-----
  list_agents           — list all agents with status
  create_agent          — scaffold a new agent from templates
  validate_agent        — run config validation, return pass/fail
  get_config            — read config.yaml
  update_config         — write config.yaml (validates before saving)
  get_system_prompt     — read prompts/system.md
  update_system_prompt  — write prompts/system.md
  chat_message          — send one message through the full pipeline
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from pathlib import Path

import yaml
from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger("aleph.mcp")

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

# TEMPLATES_DIR is always relative to this file — templates are bundled in the
# package (core/cli/templates/) so Path(__file__) is correct even after
# `uv tool install` or `pip install`.
TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "cli" / "templates"


def _get_clients_dir() -> Path:
    """Resolve clients directory at call time (not import time).

    Priority:
    1. ALEPH_CLIENTS_DIR env var   — set via ``claude mcp add ... --env``
    2. <cwd>/clients               — project root layout fallback
    """
    env_dir = os.environ.get("ALEPH_CLIENTS_DIR")
    if env_dir:
        return Path(env_dir)
    return Path.cwd() / "clients"

# ---------------------------------------------------------------------------
# Server
# ---------------------------------------------------------------------------

mcp = FastMCP(
    name="aleph",
    instructions=(
        "Manage Aleph Framework WhatsApp AI agents. "
        "Use list_agents to see what exists, create_agent to scaffold a new one, "
        "validate_agent to check config correctness, get_config / update_config to "
        "read and write config.yaml, get_system_prompt / update_system_prompt for "
        "the agent personality, and chat_message to test an agent interactively."
    ),
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent_dir(name: str) -> Path:
    """Resolve agent directory — mirrors the CLI walk-up algorithm.

    Search order:
    1. ALEPH_CLIENTS_DIR/<name>       — env var set at MCP registration time
    2. <cwd>/<name>                   — running from inside clients/
    3. <cwd>/clients/<name>           — running from project root
    4. Walk up 6 levels looking for clients/<name>
    5. Fallback: _get_clients_dir()/<name>  (produces a clear error downstream)
    """
    env_dir = os.environ.get("ALEPH_CLIENTS_DIR")
    if env_dir:
        return Path(env_dir) / name

    direct = Path.cwd() / name
    if direct.is_dir():
        return direct

    via_clients = Path.cwd() / "clients" / name
    if via_clients.is_dir():
        return via_clients

    current = Path.cwd()
    for _ in range(6):
        candidate = current / "clients" / name
        if candidate.is_dir():
            return candidate
        if current.parent == current:
            break
        current = current.parent

    return _get_clients_dir() / name


def _require_agent(name: str) -> Path:
    """Return agent dir or raise ValueError with a clear message."""
    d = _agent_dir(name)
    if not (d / "config.yaml").is_file():
        raise ValueError(
            f"Agent '{name}' not found. "
            f"Run create_agent('{name}') first, or check list_agents()."
        )
    return d


def _load_env(agent_dir: Path) -> None:
    env_path = agent_dir / ".env"
    if env_path.is_file():
        load_dotenv(env_path, override=True)


def _container_running(name: str) -> bool:
    """Check if the agent's Docker container is running."""
    result = subprocess.run(
        ["docker", "inspect", "-f", "{{.State.Running}}", f"aleph-{name}"],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() == "true"


# ---------------------------------------------------------------------------
# Tool: list_agents
# ---------------------------------------------------------------------------


@mcp.tool(description="List all agents in clients/ with their current status.")
def list_agents() -> str:
    """Returns a JSON array of agent summaries."""
    try:
        clients_dir = _get_clients_dir()
        if not clients_dir.is_dir():
            return json.dumps([])

        agents = []
        for d in sorted(clients_dir.iterdir()):
            config_path = d / "config.yaml"
            if not d.is_dir() or not config_path.is_file():
                continue
            try:
                with open(config_path) as f:
                    raw = yaml.safe_load(f) or {}
            except Exception:
                raw = {}

            agents.append({
                "name": d.name,
                "path": str(d),
                "display_name": raw.get("agent", {}).get("name", d.name),
                "model": raw.get("agent", {}).get("model", ""),
                "flows_enabled": raw.get("flows", {}).get("enabled", False),
                "habits_enabled": raw.get("habits", {}).get("enabled", False),
                "knowledge_enabled": raw.get("knowledge", {}).get("enabled", False),
                "running": _container_running(d.name),
            })

        return json.dumps(agents, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: create_agent
# ---------------------------------------------------------------------------


@mcp.tool(description="Scaffold a new agent with config, prompt, tools/, data/, and Dockerfile.")
def create_agent(
    name: str,
    description: str = "",
    model: str = "openai/gpt-4.1-mini",
    port: int = 8000,
) -> str:
    """
    Creates a new agent directory under clients/<name>.

    Args:
        name:        Agent identifier — used as directory name and client_id (e.g. 'padaria')
        description: Short description written as a comment in config.yaml
        model:       LLM model identifier (default: openai/gpt-4.1-mini)
        port:        Webhook server port (default: 8000)
    """
    try:
        agent_dir = _agent_dir(name)

        if agent_dir.exists():
            return f"Error: Agent '{name}' already exists at {agent_dir}."

        display_name = name.replace("-", " ").replace("_", " ").title()

        # Create directory structure
        (agent_dir / "prompts").mkdir(parents=True)
        (agent_dir / "tools").mkdir(parents=True)
        (agent_dir / "data").mkdir(parents=True)

        def _read_template(rel: str) -> str:
            return (TEMPLATES_DIR / rel).read_text()

        def _sub(text: str) -> str:
            return (
                text
                .replace("{{AGENT_NAME}}", name)
                .replace("{{AGENT_DISPLAY_NAME}}", display_name)
                .replace("{{MODEL}}", model)
                .replace("{{PORT}}", str(port))
            )

        # Write all scaffold files
        (agent_dir / "config.yaml").write_text(_sub(_read_template("config.yaml")))
        (agent_dir / ".env").write_text(_sub(_read_template("env.example")))
        (agent_dir / "prompts" / "system.md").write_text(_sub(_read_template("prompts/system.md")))
        (agent_dir / "tools" / ".gitkeep").touch()
        (agent_dir / "data" / ".gitkeep").touch()
        (agent_dir / "Dockerfile").write_text(_read_template("Dockerfile"))
        (agent_dir / ".dockerignore").write_text(_read_template("dockerignore"))
        (agent_dir / ".gitignore").write_text(_read_template("gitignore"))

        files_created = [
            "config.yaml", ".env", "prompts/system.md",
            "tools/", "data/", "Dockerfile",
        ]

        return (
            f"Agent '{name}' created at {agent_dir}\n\n"
            f"Files: {', '.join(files_created)}\n\n"
            f"Next steps:\n"
            f"  1. Edit {name}/.env — set REDIS_URL and your LLM API key\n"
            f"  2. Call update_system_prompt('{name}', ...) to write the agent personality\n"
            f"  3. Call validate_agent('{name}') to confirm everything is correct\n"
            f"  4. Call chat_message('{name}', 'hello') to test it\n"
        )
    except Exception as e:
        return f"Error creating agent '{name}': {e}"


# ---------------------------------------------------------------------------
# Tool: validate_agent
# ---------------------------------------------------------------------------


@mcp.tool(description="Validate an agent's config.yaml — schema, required files, and tools.")
def validate_agent(name: str) -> str:
    """
    Runs the same checks as `aleph-agent test <name>`.
    Returns PASSED or FAILED with a list of errors.

    Args:
        name: Agent name (directory name under clients/)
    """
    try:
        agent_dir = _agent_dir(name)
        config_path = agent_dir / "config.yaml"

        if not agent_dir.is_dir():
            return f"FAILED\n  ✗ Agent '{name}' not found in {_get_clients_dir()}"

        if not config_path.is_file():
            return f"FAILED\n  ✗ config.yaml not found in {agent_dir}"

        errors: list[str] = []
        warnings: list[str] = []

        # 1. YAML parse
        try:
            with open(config_path) as f:
                raw = yaml.safe_load(f)
            if not isinstance(raw, dict):
                return "FAILED\n  ✗ config.yaml is not a valid YAML mapping"
        except yaml.YAMLError as e:
            return f"FAILED\n  ✗ YAML parse error: {e}"

        # 2. Pydantic schema validation
        try:
            from core.registry.schema import FrameworkConfig
            FrameworkConfig(**raw)
        except Exception as e:
            errors.append(f"Schema validation failed: {e}")

        # 3. Required files
        prompt_file = raw.get("agent", {}).get("system_prompt_file", "prompts/system.md")
        prompt_path = agent_dir / prompt_file
        if not prompt_path.is_file():
            errors.append(f"{prompt_file} not found")
        elif len(prompt_path.read_text().strip()) < 20:
            warnings.append(f"{prompt_file} exists but is very short")

        env_path = agent_dir / ".env"
        if not env_path.is_file():
            warnings.append(".env not found (required before deployment)")
        elif "YOUR_" in env_path.read_text() or "xxx" in env_path.read_text():
            warnings.append(".env still has placeholder values")

        if not (agent_dir / "Dockerfile").is_file():
            errors.append("Dockerfile not found")

        # 4. Tools
        for t in raw.get("tools", []):
            tname = t.get("name", "unnamed")
            ttype = t.get("type", "")
            if ttype == "webhook" and not t.get("webhook_url"):
                errors.append(f"Tool '{tname}' is webhook type but has no webhook_url")
            elif ttype == "code":
                module = t.get("module", "")
                if module and not (agent_dir / "tools" / f"{module}.py").is_file():
                    errors.append(f"Tool '{tname}' references tools/{module}.py — file not found")

        # Result
        if errors:
            error_lines = "\n".join(f"  ✗ {e}" for e in errors)
            warn_lines = ("\n" + "\n".join(f"  ! {w}" for w in warnings)) if warnings else ""
            return f"FAILED\n{error_lines}{warn_lines}"

        if warnings:
            warn_lines = "\n".join(f"  ! {w}" for w in warnings)
            return f"PASSED (with warnings)\n{warn_lines}"

        return "PASSED — agent is ready to deploy"

    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: get_config
# ---------------------------------------------------------------------------


@mcp.tool(description="Read an agent's config.yaml.")
def get_config(name: str) -> str:
    """
    Returns the raw config.yaml content as text.

    Args:
        name: Agent name
    """
    try:
        agent_dir = _require_agent(name)
        return (agent_dir / "config.yaml").read_text()
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: update_config
# ---------------------------------------------------------------------------


@mcp.tool(description="Write a new config.yaml for an agent. Validates schema before saving.")
def update_config(name: str, content: str) -> str:
    """
    Validates the provided YAML content against the framework schema,
    then writes it to config.yaml. Will NOT save if validation fails.

    Args:
        name:    Agent name
        content: Full config.yaml content as a YAML string
    """
    try:
        agent_dir = _require_agent(name)

        # Parse
        try:
            raw = yaml.safe_load(content)
        except yaml.YAMLError as e:
            return f"Error: YAML parse error — {e}"

        if not isinstance(raw, dict):
            return "Error: content is not a valid YAML mapping"

        # Validate schema
        try:
            from core.registry.schema import FrameworkConfig
            FrameworkConfig(**raw)
        except Exception as e:
            return f"Error: Schema validation failed — {e}\n\nFile NOT saved."

        # Write
        (agent_dir / "config.yaml").write_text(content)
        return f"config.yaml updated for agent '{name}'."

    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: get_system_prompt
# ---------------------------------------------------------------------------


@mcp.tool(description="Read an agent's system prompt (prompts/system.md).")
def get_system_prompt(name: str) -> str:
    """
    Returns the raw prompts/system.md content.

    Args:
        name: Agent name
    """
    try:
        agent_dir = _require_agent(name)
        config_path = agent_dir / "config.yaml"
        prompt_file = "prompts/system.md"
        if config_path.is_file():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            prompt_file = raw.get("agent", {}).get("system_prompt_file", "prompts/system.md")
        prompt_path = agent_dir / prompt_file
        if not prompt_path.is_file():
            return f"Error: {prompt_file} not found for agent '{name}'"
        return prompt_path.read_text()
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: update_system_prompt
# ---------------------------------------------------------------------------


@mcp.tool(description="Write a new system prompt to prompts/system.md.")
def update_system_prompt(name: str, content: str) -> str:
    """
    Writes content to the agent's system prompt file.

    Args:
        name:    Agent name
        content: Full system prompt text (Markdown supported)
    """
    try:
        agent_dir = _require_agent(name)
        config_path = agent_dir / "config.yaml"
        prompt_file = "prompts/system.md"
        if config_path.is_file():
            with open(config_path) as f:
                raw = yaml.safe_load(f) or {}
            prompt_file = raw.get("agent", {}).get("system_prompt_file", "prompts/system.md")
        prompt_path = agent_dir / prompt_file
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(content)
        return f"System prompt updated for agent '{name}' ({len(content)} chars)."
    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Tool: chat_message
# ---------------------------------------------------------------------------


@mcp.tool(description="Send a message to an agent and get the response. Runs the full pipeline.")
async def chat_message(name: str, message: str) -> str:
    """
    Runs one message through the full agent pipeline (guardrails, tools,
    flows, LLM) and returns the response string.

    Requires the agent's .env to have REDIS_URL and an LLM API key.
    Redis is optional — flows and session state are disabled if unavailable.

    Args:
        name:    Agent name
        message: User message to send
    """
    try:
        agent_dir = _require_agent(name)
        _load_env(agent_dir)
        os.environ["AGENT_DIR"] = str(agent_dir)

        from core.engine.pipeline import process_message
        from core.registry.registry import AgentRegistry

        registry = AgentRegistry.from_config()

        # Redis — optional (flows need it, but chat works without)
        redis_session = None
        try:
            from core.session.redis import RedisSession
            redis_session = RedisSession(registry.config)
            await redis_session.connect()
        except Exception as e:
            logger.debug("Redis unavailable in MCP context (non-fatal): %s", e)

        # FlowEngine — only if flows enabled and Redis available
        flow_engine = None
        if registry.config.flows.enabled and redis_session:
            from core.flows import FlowEngine
            flow_engine = FlowEngine(registry.config.flows)

        try:
            result = await process_message(
                registry=registry,
                user_message=message,
                redis_session=redis_session,
                flow_engine=flow_engine,
                phone="mcp-chat",
            )
            return result.response
        finally:
            if redis_session:
                await redis_session.close()

    except Exception as e:
        return f"Error: {e}"


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    """Launch the MCP server over stdio."""
    logging.basicConfig(level=logging.WARNING)
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
