"""
Zuper Agent Framework — Agent Registry
========================================
The central bootstrap class that wires config → runtime objects.

Usage:
    registry = AgentRegistry.from_config(client_id="example")
    # registry.config       → FrameworkConfig (validated)
    # registry.tools        → list of tool objects
    # registry.system_prompt → string
    # registry.data         → dict of business data
    # registry.client_dir   → Path

Or from env var:
    os.environ["CLIENT_ID"] = "example"
    registry = AgentRegistry.from_config()

The Registry is intentionally passive — it loads and holds everything
but doesn't start servers or connect to Redis. That's the engine's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from core.registry.schema import FrameworkConfig
from core.registry.loader import load_config, load_system_prompt, load_data_files
from core.registry.tool_loader import load_tools, validate_tools


@dataclass
class AgentRegistry:
    """Holds all runtime objects for a single agent instance.

    Created via AgentRegistry.from_config(). Immutable after creation.
    The engine consumes this to build and run the agent.

    Attributes:
        config: Validated FrameworkConfig from the client's YAML
        tools: List of tool objects (FunctionTool instances from SDK)
        system_prompt: The agent's system prompt string
        data: Dict of business data files loaded from client's data/ dir
    """
    config: FrameworkConfig
    tools: list[Any] = field(default_factory=list)
    system_prompt: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_config(
        cls,
        client_id: str | None = None,
        config_path: Path | None = None,
        skip_tools: bool = False,
    ) -> "AgentRegistry":
        """Bootstrap the registry from a client's config.

        This is the main entry point. It:
          1. Loads and validates config.yaml (Pydantic)
          2. Loads the system prompt from prompts/*.md
          3. Loads business data files from data/
          4. Dynamically imports tool modules from tools/

        Args:
            client_id: Client identifier (e.g. 'example').
                      Falls back to CLIENT_ID env var.
            config_path: Direct path to config.yaml (overrides client_id).
            skip_tools: If True, skip tool loading (for validation without SDK).

        Returns:
            Fully loaded AgentRegistry ready for the engine.

        Raises:
            FileNotFoundError: missing config, prompt, or data files
            pydantic.ValidationError: invalid config
            ImportError: tool module issues
        """
        # 1. Load and validate config
        config = load_config(client_id=client_id, config_path=config_path)

        # 2. Load system prompt
        system_prompt = load_system_prompt(config)

        # 3. Load business data
        data = load_data_files(config)

        # 4. Load tools
        tools = []
        if not skip_tools:
            try:
                tools = load_tools(config)
            except ImportError as e:
                # If SDK isn't installed, fall back to validation-only mode
                import warnings
                warnings.warn(
                    f"Tool loading failed (SDK may not be installed): {e}\n"
                    f"Running in validation-only mode.",
                    stacklevel=2,
                )
                tools = []

        return cls(
            config=config,
            tools=tools,
            system_prompt=system_prompt,
            data=data,
        )

    # -------------------------------------------------------------------
    # Convenience properties
    # -------------------------------------------------------------------

    @property
    def client_id(self) -> str:
        return self.config.client_id

    @property
    def client_dir(self) -> Path:
        return self.config.client_dir

    @property
    def agent_name(self) -> str:
        return self.config.agent.name

    @property
    def model(self) -> str:
        return self.config.agent.model

    @property
    def fallback_model(self) -> str:
        return self.config.agent.fallback_model

    # -------------------------------------------------------------------
    # Introspection
    # -------------------------------------------------------------------

    def summary(self) -> str:
        """Human-readable summary of the loaded registry."""
        lines = [
            "=" * 60,
            "🚀 Zuper Agent Registry — Boot Summary",
            "=" * 60,
            f"  Client:          {self.client_id}",
            f"  Agent:           {self.agent_name}",
            f"  Model:           {self.model}",
            f"  Fallback:        {self.fallback_model}",
            f"  Client dir:      {self.client_dir}",
            "",
            "  SDK Features:",
            f"    Sessions:      {'ON' if self.config.sdk.sessions.enabled else 'OFF'}",
            f"    Guardrails:    {'ON' if self.config.sdk.guardrails.enabled else 'OFF'}",
            f"    Handoffs:      {'ON' if self.config.sdk.handoffs.enabled else 'OFF'}",
            "",
            "  Debug:",
            f"    Tracing:       {'ON' if self.config.debug.tracing.enabled else 'OFF'} → {self.config.debug.tracing.export_to}",
            f"    Log level:     {self.config.debug.logging.level.value}",
            f"    Dry run:       {self.config.debug.dry_run}",
            "",
            "  Always ON:",
            f"    Buffer:        {self.config.session.buffer_timeout}s",
            f"    Anti-spam:     {self.config.session.antispam_ttl}s",
            f"    Lock:          {self.config.session.processing_lock_ttl}s",
            "",
            "  Features:",
            f"    Human HITL:    {'ON' if self.config.human.enabled else 'OFF'}",
            f"    Habits:        {'ON' if self.config.habits.enabled else 'OFF'}",
            f"    Follow-up:     {'ON' if self.config.follow_up.enabled else 'OFF'}",
            f"    Media:         {'ON' if self.config.media.enabled else 'OFF'}",
            f"    Queue:         {'ON' if self.config.queue.enabled else 'OFF'}",
            "",
            f"  Tools loaded:    {len(self.tools)}",
        ]
        for t in self.tools:
            name = getattr(t, "name", getattr(t, "__name__", str(t)))
            lines.append(f"    - {name}")

        lines.extend([
            "",
            f"  Data files:      {list(self.data.keys()) or '(none)'}",
            f"  Prompt:          {len(self.system_prompt)} chars",
            f"  Guardrails:      {len(self.config.guardrails.input_patterns)} input, {len(self.config.guardrails.output_rules)} output",
            "=" * 60,
        ])
        return "\n".join(lines)

    def validate_only(self) -> dict:
        """Quick validation without loading tools (no SDK needed).
        Returns a dict with validation results."""
        return {
            "config_valid": True,
            "client_id": self.client_id,
            "agent_name": self.agent_name,
            "prompt_loaded": bool(self.system_prompt),
            "prompt_length": len(self.system_prompt),
            "data_files_loaded": list(self.data.keys()),
            "tools_validation": validate_tools(self.config),
        }


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    """CLI: python -m core.registry --client <id>"""
    import argparse

    parser = argparse.ArgumentParser(description="Zuper Agent Framework — Registry Boot")
    parser.add_argument("--client", type=str, help="Client ID (e.g. 'example')")
    parser.add_argument("--path", type=str, help="Direct path to config.yaml")
    parser.add_argument("--skip-tools", action="store_true", help="Skip tool loading (no SDK needed)")
    parser.add_argument("--validate", action="store_true", help="Validate only, don't load tools")
    args = parser.parse_args()

    config_path = Path(args.path) if args.path else None

    try:
        registry = AgentRegistry.from_config(
            client_id=args.client,
            config_path=config_path,
            skip_tools=args.skip_tools or args.validate,
        )

        if args.validate:
            import json
            result = registry.validate_only()
            print(json.dumps(result, indent=2, default=str))
        else:
            print(registry.summary())

    except Exception as e:
        print(f"❌ Boot failed: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    import sys
    main()
