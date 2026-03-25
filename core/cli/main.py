"""
Zuper Agent Framework — CLI
=============================
Entry point for the `zuper-agent` command.

Commands:
  zuper-agent init <name>     Create a new agent scaffold
  zuper-agent start [name]    Build & run the agent container
  zuper-agent stop [name]     Stop the agent container
  zuper-agent test [name]     Validate config + boot check
  zuper-agent list            List agents in current directory
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import typer
from rich.console import Console
from rich.panel import Panel

app = typer.Typer(
    name="zuper-agent",
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
    """Resolve agent directory relative to cwd."""
    return Path.cwd() / name


def _require_docker():
    """Check that docker is available."""
    if not shutil.which("docker"):
        console.print("[red]✗[/red] Docker not found. Install Docker to use start/stop commands.")
        raise typer.Exit(1)


def _container_name(agent_name: str) -> str:
    """Standard container name for an agent."""
    return f"zuper-agent-{agent_name}"


def _image_name(agent_name: str) -> str:
    """Standard image name for an agent."""
    return f"zuper-agent-{agent_name}:latest"


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
        title="zuper-agent init",
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
    console.print(f"  4. Run [cyan]zuper-agent test {name}[/cyan] to validate")
    console.print(f"  5. Run [cyan]zuper-agent start {name}[/cyan] to launch")


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
        console.print(f"  Run [cyan]zuper-agent init {name}[/cyan] first.")
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
        title="zuper-agent start",
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
        console.print(f"  Stop:    [cyan]zuper-agent stop {name}[/cyan]")
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
        title="zuper-agent test",
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
        # zuper-agent not installed — do basic field checks
        console.print("  [yellow]![/yellow] zuper-agent package not installed, doing basic checks")
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
        console.print(f"  Run [cyan]zuper-agent init <name>[/cyan] to create one.")


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
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app()
