"""
Aleph Framework — CLI
=============================
Entry point for the `aleph` command.

Commands:
  aleph init <name>     Create a new agent scaffold
  aleph start [name]    Build & run the agent container
  aleph stop [name]     Stop the agent container
  aleph test [name]     Validate config + boot check
  aleph list            List agents in current directory
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path

import asyncio
import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    name="aleph",
    help="Config-driven framework for WhatsApp AI agents",
    add_completion=False,
    no_args_is_help=True,
)

console = Console()

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEMPLATES_DIR = Path(__file__).parent / "templates"


def _agent_dir(name: str) -> Path:
    """Resolve agent directory.

    Search order:
    1. <cwd>/<name>              — running from inside clients/
    2. <cwd>/clients/<name>      — running from project root
    3. Walk up to find clients/  — running from any subdirectory
    """
    # 1. Direct: running from inside clients/
    direct = Path.cwd() / name
    if direct.is_dir():
        return direct

    # 2. Project root: clients/ sibling of cwd
    via_clients = Path.cwd() / "clients" / name
    if via_clients.is_dir():
        return via_clients

    # 3. Walk up looking for a clients/ directory that contains this agent
    current = Path.cwd()
    for _ in range(6):  # max 6 levels up — avoid infinite loops on weird mounts
        candidate = current / "clients" / name
        if candidate.is_dir():
            return candidate
        if current.parent == current:
            break
        current = current.parent

    # Fallback: original behaviour (will produce a clear "not found" error downstream)
    return Path.cwd() / name


def _require_docker():
    """Check that docker is available."""
    if not shutil.which("docker"):
        console.print("[red]✗[/red] Docker not found. Install Docker to use start/stop commands.")
        raise typer.Exit(1)


def _container_name(agent_name: str) -> str:
    """Standard container name for an agent."""
    return f"aleph-{agent_name}"


def _image_name(agent_name: str) -> str:
    """Standard image name for an agent."""
    return f"aleph-{agent_name}:latest"


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a subprocess with output visible to user."""
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd, cwd=cwd, capture_output=False)
    if check and result.returncode != 0:
        console.print(f"[red]✗[/red] Command failed with exit code {result.returncode}")
        raise typer.Exit(result.returncode)
    return result


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------

@app.command()
def init(
    name: str = typer.Argument(help="Agent name (e.g. 'padaria', 'clinica')"),
    port: int = typer.Option(8000, "--port", "-p", help="Server port for this agent"),
    model: str = typer.Option(
        "openai/gpt-4.1-mini", "--model", "-m",
        help="LLM model identifier",
    ),
):
    """Create a new agent scaffold with config, prompt, Dockerfile, and .env."""
    agent_dir = _agent_dir(name)

    if agent_dir.exists():
        console.print(f"[red]✗[/red] Directory already exists: {agent_dir}")
        raise typer.Exit(1)

    console.print(Panel(
        f"Creating agent [bold cyan]{name}[/bold cyan] at {agent_dir}",
        title="aleph init",
        border_style="green",
    ))

    # Create directory structure
    (agent_dir / "prompts").mkdir(parents=True)
    (agent_dir / "tools").mkdir(parents=True)
    (agent_dir / "data").mkdir(parents=True)

    # --- config.yaml ---
    config_template = (TEMPLATES_DIR / "config.yaml").read_text()
    config_content = (
        config_template
        .replace("{{AGENT_NAME}}", name)
        .replace("{{AGENT_DISPLAY_NAME}}", name.replace("-", " ").title())
        .replace("{{MODEL}}", model)
        .replace("{{PORT}}", str(port))
    )
    (agent_dir / "config.yaml").write_text(config_content)

    # --- .env ---
    env_template = (TEMPLATES_DIR / "env.example").read_text()
    env_content = env_template.replace("{{AGENT_NAME}}", name)
    (agent_dir / ".env").write_text(env_content)

    # --- prompts/system.md ---
    prompt_template = (TEMPLATES_DIR / "prompts" / "system.md").read_text()
    prompt_content = prompt_template.replace(
        "{{AGENT_DISPLAY_NAME}}", name.replace("-", " ").title(),
    )
    (agent_dir / "prompts" / "system.md").write_text(prompt_content)

    # --- tools/.gitkeep ---
    (agent_dir / "tools" / ".gitkeep").touch()

    # --- data/.gitkeep ---
    (agent_dir / "data" / ".gitkeep").touch()

    # --- Dockerfile ---
    dockerfile_template = (TEMPLATES_DIR / "Dockerfile").read_text()
    (agent_dir / "Dockerfile").write_text(dockerfile_template)

    # --- .dockerignore ---
    dockerignore_template = (TEMPLATES_DIR / "dockerignore").read_text()
    (agent_dir / ".dockerignore").write_text(dockerignore_template)

    # --- .gitignore ---
    gitignore_template = (TEMPLATES_DIR / "gitignore").read_text()
    (agent_dir / ".gitignore").write_text(gitignore_template)

    console.print()
    console.print("[green]✓[/green] Agent scaffold created:")
    console.print()

    # Show tree
    for item in sorted(agent_dir.rglob("*")):
        rel = item.relative_to(agent_dir)
        indent = "  " * (len(rel.parts) - 1)
        icon = "📁" if item.is_dir() else "📄"
        console.print(f"  {indent}{icon} {rel.name}")

    console.print()
    console.print("[bold]Next steps:[/bold]")
    console.print(f"  1. Edit [cyan]{name}/.env[/cyan] with your API keys and Z-API credentials")
    console.print(f"  2. Edit [cyan]{name}/prompts/system.md[/cyan] with your agent's personality")
    console.print(f"  3. Edit [cyan]{name}/config.yaml[/cyan] to add guardrails and tools")
    console.print(f"  4. Run [cyan]aleph test {name}[/cyan] to validate")
    console.print(f"  5. Run [cyan]aleph start {name}[/cyan] to launch")


# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

@app.command()
def start(
    name: str = typer.Argument(help="Agent name (directory name)"),
    build: bool = typer.Option(True, "--build/--no-build", help="Build image before starting"),
    detach: bool = typer.Option(True, "--detach/--no-detach", "-d", help="Run in background"),
    port: int = typer.Option(None, "--port", "-p", help="Port override (default: from config.yaml)"),
):
    """Build the Docker image and start the agent container."""
    _require_docker()

    agent_dir = _agent_dir(name)
    if not agent_dir.is_dir():
        console.print(f"[red]✗[/red] Agent directory not found: {agent_dir}")
        console.print(f"  Run [cyan]aleph init {name}[/cyan] first.")
        raise typer.Exit(1)

    if not (agent_dir / "Dockerfile").is_file():
        console.print(f"[red]✗[/red] No Dockerfile found in {agent_dir}")
        raise typer.Exit(1)

    image = _image_name(name)
    container = _container_name(name)

    # Resolve port from config if not overridden
    if port is None:
        port = _read_port_from_config(agent_dir)

    console.print(Panel(
        f"Starting agent [bold cyan]{name}[/bold cyan]\n"
        f"  Image:     {image}\n"
        f"  Container: {container}\n"
        f"  Port:      {port}",
        title="aleph start",
        border_style="green",
    ))

    # Build
    if build:
        console.print("\n[bold]Building image...[/bold]")
        _run(["docker", "build", "-t", image, "."], cwd=agent_dir)

    # Stop existing container if running
    subprocess.run(
        ["docker", "rm", "-f", container],
        capture_output=True,
    )

    # Run
    console.print("\n[bold]Starting container...[/bold]")
    cmd = [
        "docker", "run",
        "--name", container,
        "--env-file", str(agent_dir / ".env"),
        "-p", f"{port}:8000",
        "--restart", "unless-stopped",
    ]

    # Add network if zuper_net exists
    net_check = subprocess.run(
        ["docker", "network", "inspect", "zuper_net"],
        capture_output=True,
    )
    if net_check.returncode == 0:
        cmd.extend(["--network", "zuper_net"])

    if detach:
        cmd.append("-d")

    cmd.append(image)
    _run(cmd)

    if detach:
        console.print()
        console.print(f"[green]✓[/green] Agent [bold]{name}[/bold] is running on port {port}")
        console.print()
        console.print("[bold]Useful commands:[/bold]")
        console.print(f"  Logs:    [cyan]docker logs -f {container}[/cyan]")
        console.print(f"  Stop:    [cyan]aleph stop {name}[/cyan]")
        console.print(f"  Health:  [cyan]curl http://localhost:{port}/health[/cyan]")


# ---------------------------------------------------------------------------
# stop
# ---------------------------------------------------------------------------

@app.command()
def stop(
    name: str = typer.Argument(help="Agent name"),
):
    """Stop and remove the agent container."""
    _require_docker()

    container = _container_name(name)

    console.print(f"Stopping [bold]{container}[/bold]...")
    result = subprocess.run(
        ["docker", "rm", "-f", container],
        capture_output=True, text=True,
    )

    if result.returncode == 0:
        console.print(f"[green]✓[/green] Agent [bold]{name}[/bold] stopped and removed.")
    else:
        console.print(f"[yellow]![/yellow] Container {container} not found or already stopped.")


# ---------------------------------------------------------------------------
# test
# ---------------------------------------------------------------------------

@app.command()
def test(
    name: str = typer.Argument(help="Agent name (directory name)"),
):
    """Validate agent config.yaml and check boot readiness."""
    agent_dir = _agent_dir(name)

    if not agent_dir.is_dir():
        console.print(f"[red]✗[/red] Agent directory not found: {agent_dir}")
        raise typer.Exit(1)

    config_path = agent_dir / "config.yaml"
    if not config_path.is_file():
        console.print(f"[red]✗[/red] config.yaml not found in {agent_dir}")
        raise typer.Exit(1)

    console.print(Panel(
        f"Validating agent [bold cyan]{name}[/bold cyan]",
        title="aleph test",
        border_style="blue",
    ))

    errors = []
    warnings = []

    # 1. Parse YAML
    console.print("\n[bold]1. YAML syntax[/bold]")
    try:
        import yaml
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        if not isinstance(raw, dict):
            errors.append("config.yaml is not a valid YAML mapping")
            console.print("  [red]✗[/red] Not a valid YAML mapping")
        else:
            console.print("  [green]✓[/green] YAML syntax OK")
    except yaml.YAMLError as e:
        errors.append(f"YAML parse error: {e}")
        console.print(f"  [red]✗[/red] YAML parse error: {e}")
        _print_result(errors, warnings)
        raise typer.Exit(1)

    # 2. Pydantic validation
    console.print("\n[bold]2. Schema validation[/bold]")
    try:
        from core.registry.schema import FrameworkConfig
        config = FrameworkConfig(**raw)
        console.print("  [green]✓[/green] Schema validation OK")
        console.print(f"      Client ID: {config.client_id}")
        console.print(f"      Agent:     {config.agent.name}")
        console.print(f"      Model:     {config.agent.model}")
    except ImportError:
        # aleph not installed — do basic field checks
        console.print("  [yellow]![/yellow] aleph package not installed, doing basic checks")
        _basic_field_check(raw, errors, warnings)
    except Exception as e:
        errors.append(f"Schema validation failed: {e}")
        console.print(f"  [red]✗[/red] {e}")

    # 3. Files check
    console.print("\n[bold]3. Required files[/bold]")

    prompt_file = raw.get("agent", {}).get("system_prompt_file", "prompts/system.md")
    prompt_path = agent_dir / prompt_file
    if prompt_path.is_file():
        content = prompt_path.read_text().strip()
        if len(content) < 20:
            warnings.append(f"System prompt is very short ({len(content)} chars)")
            console.print(f"  [yellow]![/yellow] {prompt_file} — exists but very short ({len(content)} chars)")
        else:
            console.print(f"  [green]✓[/green] {prompt_file} — {len(content)} chars")
    else:
        errors.append(f"System prompt not found: {prompt_file}")
        console.print(f"  [red]✗[/red] {prompt_file} — not found")

    env_path = agent_dir / ".env"
    if env_path.is_file():
        env_content = env_path.read_text()
        if "YOUR_" in env_content or "xxx" in env_content:
            warnings.append(".env still has placeholder values")
            console.print("  [yellow]![/yellow] .env — has placeholder values (edit before deploying)")
        else:
            console.print("  [green]✓[/green] .env — present")
    else:
        warnings.append(".env not found (needed for deployment)")
        console.print("  [yellow]![/yellow] .env — not found (create before deploying)")

    dockerfile_path = agent_dir / "Dockerfile"
    if dockerfile_path.is_file():
        console.print("  [green]✓[/green] Dockerfile — present")
    else:
        errors.append("Dockerfile not found")
        console.print("  [red]✗[/red] Dockerfile — not found")

    # 4. Tools check
    console.print("\n[bold]4. Tools[/bold]")
    tools_config = raw.get("tools", [])
    if tools_config:
        for t in tools_config:
            tname = t.get("name", "unnamed")
            ttype = t.get("type", "unknown")
            if ttype == "webhook":
                url = t.get("webhook_url", "")
                if not url:
                    errors.append(f"Tool '{tname}' is webhook type but has no webhook_url")
                    console.print(f"  [red]✗[/red] {tname} (webhook) — missing webhook_url")
                else:
                    console.print(f"  [green]✓[/green] {tname} (webhook) → {url[:60]}")
            elif ttype == "code":
                module = t.get("module", "")
                mod_path = agent_dir / "tools" / f"{module}.py"
                if mod_path.is_file():
                    console.print(f"  [green]✓[/green] {tname} (code) → tools/{module}.py")
                else:
                    errors.append(f"Tool '{tname}' references tools/{module}.py but file not found")
                    console.print(f"  [red]✗[/red] {tname} (code) → tools/{module}.py not found")
            else:
                console.print(f"  [dim]  {tname} ({ttype})[/dim]")
    else:
        console.print("  [dim]  No tools configured[/dim]")

    # Result
    _print_result(errors, warnings)

    if errors:
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------

@app.command(name="list")
def list_agents():
    """List agent directories in the current folder."""
    cwd = Path.cwd()
    agents = []

    for d in sorted(cwd.iterdir()):
        if d.is_dir() and (d / "config.yaml").is_file():
            agents.append(d.name)

    if agents:
        console.print("[bold]Agents found:[/bold]")
        for a in agents:
            # Check if container is running
            container = _container_name(a)
            result = subprocess.run(
                ["docker", "inspect", "-f", "{{.State.Running}}", container],
                capture_output=True, text=True,
            )
            running = result.stdout.strip() == "true" if result.returncode == 0 else False
            status = "[green]● running[/green]" if running else "[dim]○ stopped[/dim]"
            console.print(f"  {status}  {a}")
    else:
        console.print("[dim]No agents found in current directory.[/dim]")
        console.print(f"  Run [cyan]aleph init <name>[/cyan] to create one.")


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _read_port_from_config(agent_dir: Path) -> int:
    """Read port from config.yaml, fallback to 8000."""
    config_path = agent_dir / "config.yaml"
    if config_path.is_file():
        import yaml
        with open(config_path) as f:
            raw = yaml.safe_load(f)
        if isinstance(raw, dict):
            return raw.get("api", {}).get("port", 8000)
    return 8000


def _basic_field_check(raw: dict, errors: list, warnings: list):
    """Basic field validation when Pydantic schema isn't available."""
    if "client_id" not in raw:
        errors.append("Missing required field: client_id")
        console.print("  [red]✗[/red] Missing: client_id")
    else:
        console.print(f"  [green]✓[/green] client_id: {raw['client_id']}")

    agent = raw.get("agent", {})
    if not agent.get("name"):
        errors.append("Missing required field: agent.name")
        console.print("  [red]✗[/red] Missing: agent.name")
    else:
        console.print(f"  [green]✓[/green] agent.name: {agent['name']}")

    if not agent.get("model"):
        errors.append("Missing required field: agent.model")
        console.print("  [red]✗[/red] Missing: agent.model")
    else:
        console.print(f"  [green]✓[/green] agent.model: {agent['model']}")

    if "human" not in raw:
        warnings.append("No 'human' section — HITL will use defaults")
        console.print("  [yellow]![/yellow] No 'human' section (will use defaults)")


def _print_result(errors: list, warnings: list):
    """Print the final test result summary."""
    console.print()
    if errors:
        console.print(Panel(
            f"[red]{len(errors)} error(s)[/red], {len(warnings)} warning(s)\n\n"
            + "\n".join(f"  ✗ {e}" for e in errors),
            title="FAILED",
            border_style="red",
        ))
    elif warnings:
        console.print(Panel(
            f"[green]Validation passed[/green] with {len(warnings)} warning(s)\n\n"
            + "\n".join(f"  ! {w}" for w in warnings),
            title="PASSED (with warnings)",
            border_style="yellow",
        ))
    else:
        console.print(Panel(
            "[green]All checks passed — agent is ready to deploy![/green]",
            title="PASSED",
            border_style="green",
        ))

# ---------------------------------------------------------------------------
# knowledge
# ---------------------------------------------------------------------------

@app.command()
def knowledge(
    action: str = typer.Argument(help="Action: load, list, clear"),
    name: str = typer.Argument(help="Agent name (directory name)"),
    file: str = typer.Option(None, "--file", "-f", help="File to load"),
    dir_path: str = typer.Option(None, "--dir", "-d", help="Directory to load"),
    source: str = typer.Option(None, "--source", "-s", help="Source filter (for clear)"),
):
    """Manage agent knowledge base: load, list, clear."""
    agent_dir = _agent_dir(name)
    if not agent_dir.is_dir():
        console.print(f"[red]✗[/red] Agent directory not found: {agent_dir}")
        raise typer.Exit(1)

    config_path = agent_dir / "config.yaml"
    if not config_path.is_file():
        console.print(f"[red]✗[/red] config.yaml not found in {agent_dir}")
        raise typer.Exit(1)

    # Load config
    import yaml
    with open(config_path) as f:
        raw = yaml.safe_load(f)

    client_id = raw.get("client_id", name)

    # Load .env if present
    env_path = agent_dir / ".env"
    if env_path.is_file():
        from dotenv import load_dotenv
        load_dotenv(env_path)

    if action == "load":
        asyncio.run(_knowledge_load(client_id, raw, file, dir_path, agent_dir))
    elif action == "list":
        asyncio.run(_knowledge_list(client_id, raw))
    elif action == "clear":
        asyncio.run(_knowledge_clear(client_id, raw, source))
    else:
        console.print(f"[red]✗[/red] Unknown action: {action}. Use: load, list, clear")
        raise typer.Exit(1)


async def _knowledge_load(client_id: str, raw: dict, file: str | None, dir_path: str | None, agent_dir: Path):
    """Load files into knowledge base."""
    if not file and not dir_path:
        console.print("[red]✗[/red] Specify --file or --dir")
        raise typer.Exit(1)

    from core.registry.schema import KnowledgeConfig
    from core.knowledge.database import KnowledgeDatabase
    from core.knowledge.loader import load_file, load_directory
    from core.knowledge.ingest import ingest_documents

    knowledge_raw = raw.get("knowledge", {})
    config = KnowledgeConfig(**knowledge_raw)

    if not config.enabled:
        console.print("[red]✗[/red] knowledge.enabled=false in config.yaml")
        raise typer.Exit(1)

    db = KnowledgeDatabase(config)
    await db.connect()
    await db.bootstrap()

    try:
        docs = []
        if file:
            path = Path(file)
            if not path.is_absolute():
                path = agent_dir / path
            docs.append(load_file(path))
            console.print(f"[green]✓[/green] Loaded: {path.name} ({len(docs[0].content)} chars)")

        if dir_path:
            path = Path(dir_path)
            if not path.is_absolute():
                path = agent_dir / path
            loaded = load_directory(path)
            docs.extend(loaded)
            console.print(f"[green]✓[/green] Loaded {len(loaded)} files from {path}")

        if not docs:
            console.print("[yellow]![/yellow] No files loaded")
            return

        console.print(f"\n[bold]Ingesting {len(docs)} document(s)...[/bold]")
        total = await ingest_documents(db, config, client_id, docs)
        console.print(f"\n[green]✓[/green] Ingestion complete: {total} chunks stored")

    finally:
        await db.close()


async def _knowledge_list(client_id: str, raw: dict):
    """List knowledge base contents."""
    from core.registry.schema import KnowledgeConfig
    from core.knowledge.database import KnowledgeDatabase

    knowledge_raw = raw.get("knowledge", {})
    config = KnowledgeConfig(**knowledge_raw)

    if not config.enabled:
        console.print("[red]✗[/red] knowledge.enabled=false in config.yaml")
        raise typer.Exit(1)

    schema = config.schema
    table = config.table_name
    full_table = f"{schema}.{table}" if schema != "public" else table

    db = KnowledgeDatabase(config)
    await db.connect()

    try:
        async with db.pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT source, COUNT(*) as chunks, 
                       SUM(LENGTH(content)) as total_chars,
                       MIN(created_at) as first_added
                FROM {full_table}
                WHERE client_id = $1
                GROUP BY source
                ORDER BY source
                """,
                client_id,
            )

        if rows:
            console.print(f"\n[bold]Knowledge base for '{client_id}':[/bold]\n")
            total_chunks = 0
            for row in rows:
                chunks = row["chunks"]
                total_chunks += chunks
                chars = row["total_chars"]
                source = row["source"] or "(unknown)"
                console.print(f"  📄 {source} — {chunks} chunks, {chars:,} chars")

            console.print(f"\n  Total: {total_chunks} chunks across {len(rows)} source(s)")
        else:
            console.print(f"[dim]No knowledge base entries for '{client_id}'[/dim]")

    finally:
        await db.close()


async def _knowledge_clear(client_id: str, raw: dict, source: str | None):
    """Clear knowledge base entries."""
    from core.registry.schema import KnowledgeConfig
    from core.knowledge.database import KnowledgeDatabase

    knowledge_raw = raw.get("knowledge", {})
    config = KnowledgeConfig(**knowledge_raw)

    if not config.enabled:
        console.print("[red]✗[/red] knowledge.enabled=false in config.yaml")
        raise typer.Exit(1)

    schema = config.schema
    table = config.table_name
    full_table = f"{schema}.{table}" if schema != "public" else table

    db = KnowledgeDatabase(config)
    await db.connect()

    try:
        async with db.pool.acquire() as conn:
            if source:
                result = await conn.execute(
                    f"DELETE FROM {full_table} WHERE client_id = $1 AND source = $2",
                    client_id, source,
                )
                console.print(f"[green]✓[/green] Cleared chunks from source '{source}': {result}")
            else:
                result = await conn.execute(
                    f"DELETE FROM {full_table} WHERE client_id = $1",
                    client_id,
                )
                console.print(f"[green]✓[/green] Cleared all knowledge for '{client_id}': {result}")

    finally:
        await db.close()

# ---------------------------------------------------------------------------
# chat (interactive runner)
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# chat (interactive runner)
# ---------------------------------------------------------------------------

@app.command()
def chat(
    name: str = typer.Argument(help="Agent name (directory name)"),
    log_level: str = typer.Option("INFO", "--log-level", "-l", help="Log level"),
):
    """Interactive chat with the agent (terminal mode, no WhatsApp needed)."""
    agent_dir = _agent_dir(name)
    if not agent_dir.is_dir():
        console.print(f"[red]✗[/red] Agent directory not found: {agent_dir}")
        raise typer.Exit(1)

    # Load .env
    env_path = agent_dir / ".env"
    if env_path.is_file():
        from dotenv import load_dotenv
        load_dotenv(env_path, override=True)

    # Set AGENT_DIR so the framework finds config/prompts/tools here
    os.environ["AGENT_DIR"] = str(agent_dir)

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    asyncio.run(_chat_loop(name, agent_dir))


async def _chat_loop(name: str, agent_dir):
    """Main chat loop — everything runs in a single event loop."""
    from core.registry.registry import AgentRegistry
    from core.engine.pipeline import process_message

    # Boot registry
    try:
        registry = AgentRegistry.from_config()
        console.print(f"\n[green]✓[/green] Agent loaded: [bold]{registry.agent_name}[/bold]")
        console.print(f"  Model: {registry.config.agent.model}")
        console.print(f"  Knowledge: {'ON' if registry.config.knowledge.enabled else 'OFF'}")
        console.print(f"  Habits: {'ON' if registry.config.habits.enabled else 'OFF'}")
    except Exception as e:
        console.print(f"[red]✗[/red] Boot failed: {e}")
        return

    # Init Redis (required for flows, habits, anti-spam)
    redis_session = None
    try:
        from core.session.redis import RedisSession
        redis_session = RedisSession(registry.config)
        await redis_session.connect()
        console.print(f"  [green]✓[/green] Redis connected")
    except ValueError:
        console.print(
            "  [yellow]![/yellow] Redis not configured — "
            "set [cyan]REDIS_URL[/cyan] in your .env to enable flows and session state"
        )
    except Exception as e:
        console.print(
            f"  [yellow]![/yellow] Redis unreachable ({_redis_hint(e)}) — "
            "flows and session state disabled"
        )
        redis_session = None

    # Init knowledge DB if enabled
    knowledge_db = None
    if registry.config.knowledge.enabled:
        try:
            from core.knowledge.database import KnowledgeDatabase
            knowledge_db = KnowledgeDatabase(registry.config.knowledge)
            await knowledge_db.connect()
            await knowledge_db.bootstrap()
            console.print(f"  [green]✓[/green] Knowledge DB connected")
        except Exception as e:
            console.print(f"  [yellow]![/yellow] Knowledge DB failed: {e}")

    # Init FlowEngine (only if flows.enabled)
    flow_engine = None
    if registry.config.flows.enabled:
        if redis_session is None:
            console.print(
                "  [yellow]![/yellow] Flows disabled — Redis is required "
                "(set [cyan]REDIS_URL[/cyan] in your .env)"
            )
        else:
            from core.flows import FlowEngine
            flow_engine = FlowEngine(registry.config.flows)
            console.print(f"  [green]✓[/green] Flows: {len(registry.config.flows.flows)} flow(s) loaded")

    console.print(f"\n💬 Interactive mode — type 'quit' to exit")
    console.print("-" * 40)

    history = []

    while True:
        try:
            user_input = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n👤 You: ").strip()
            )
        except (EOFError, KeyboardInterrupt):
            console.print("\n👋 Bye!")
            break

        if user_input.lower() in ("quit", "exit", "q"):
            console.print("👋 Bye!")
            break

        if not user_input:
            continue

        result = await process_message(
            registry=registry,
            user_message=user_input,
            message_history=history,
            knowledge_db=knowledge_db,
            flow_engine=flow_engine,
            redis_session=redis_session,
            phone="chat-local",
        )

        # Show guardrail info
        if result.input_classification and result.input_classification.matched:
            cls = result.input_classification
            console.print(f"  🛡️ Guardrail: {cls.pattern_name} → {cls.action.value}")

        if result.output_check and result.output_check.blocked:
            console.print(f"  🚫 Output blocked: {result.output_check.rule_name}")

        if result.skipped_llm:
            console.print(f"  ⚡ LLM skipped (guardrail handled)")

        if result.habit_used:
            console.print(f"  📚 Habit context used")

        console.print(f"\n🤖 {registry.agent_name}: {result.response}")
        console.print(f"  ⏱️ {result.elapsed_seconds:.1f}s")

        history.append({"role": "user", "content": user_input})
        history.append({"role": "assistant", "content": result.response})

    # Cleanup
    if knowledge_db:
        await knowledge_db.close()
    if redis_session:
        await redis_session.close()


async def _connect_knowledge(db):
    await db.connect()


async def _close_knowledge(db):
    await db.close()


def _redis_hint(exc: Exception) -> str:
    """Turn a raw Redis exception into a one-line human hint."""
    msg = str(exc).lower()
    if "connection refused" in msg:
        return "connection refused — is Redis running?"
    if "auth" in msg or "password" in msg or "noauth" in msg:
        return "authentication failed — check the password in REDIS_URL"
    if "timeout" in msg:
        return "timed out — check host/port in REDIS_URL"
    if "name or service not known" in msg or "nodename" in msg:
        return "host not found — check the hostname in REDIS_URL"
    return str(exc)[:80]


async def _chat_message(registry, user_input, history, knowledge_db, flow_engine=None, redis_session=None):
    from core.engine.pipeline import process_message
    return await process_message(
        registry=registry,
        user_message=user_input,
        message_history=history,
        knowledge_db=knowledge_db,
        flow_engine=flow_engine,
        redis_session=redis_session,
        phone="chat-local",
    )

# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
