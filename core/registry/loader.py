"""
Zuper Agent Framework — Config Loader
======================================
Reads a client's config.yaml, validates it against the Pydantic schema,
and returns a typed FrameworkConfig object.

Resolves the client directory from:
  1. Explicit path argument
  2. CLIENT_ID env var → clients/<CLIENT_ID>/config.yaml
  3. CLI flag --client <id>

Usage (mini-run):
  python -m core.registry.loader --client example
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

from core.registry.schema import FrameworkConfig


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_CONFIG_FILENAME = "config.yaml"
CLIENTS_DIR = Path("clients")


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

def resolve_client_dir(client_id: str | None = None) -> Path:
    """Resolve the client directory path.

    Priority:
      1. Explicit client_id argument
      2. CLIENT_ID environment variable

    Returns:
        Path to the client directory (e.g. clients/example/)

    Raises:
        FileNotFoundError: if client dir doesn't exist
    """
    cid = client_id or os.environ.get("CLIENT_ID")
    if not cid:
        raise ValueError(
            "No client specified. Pass client_id or set CLIENT_ID env var."
        )

    client_dir = CLIENTS_DIR / cid
    if not client_dir.is_dir():
        raise FileNotFoundError(
            f"Client directory not found: {client_dir}\n"
            f"Available clients: {_list_clients()}"
        )
    return client_dir


def load_config(client_id: str | None = None, config_path: Path | None = None) -> FrameworkConfig:
    """Load and validate a client's config.yaml.

    Args:
        client_id: Client identifier (e.g. 'example'). Resolves to clients/<id>/config.yaml.
        config_path: Explicit path to config.yaml (overrides client_id).

    Returns:
        Validated FrameworkConfig object with all defaults populated.

    Raises:
        FileNotFoundError: if config file doesn't exist
        ValueError: if no client specified
        pydantic.ValidationError: if config is invalid (with clear error message)
    """
    if config_path:
        path = Path(config_path)
    else:
        client_dir = resolve_client_dir(client_id)
        path = client_dir / DEFAULT_CONFIG_FILENAME

    if not path.is_file():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"Config file is not a valid YAML mapping: {path}")

    config = FrameworkConfig(**raw)
    return config


def load_system_prompt(config: FrameworkConfig) -> str:
    """Load the system prompt markdown file for a client.

    Args:
        config: Validated FrameworkConfig

    Returns:
        System prompt as string

    Raises:
        FileNotFoundError: if prompt file doesn't exist
    """
    prompt_path = config.client_dir / config.agent.system_prompt_file
    if not prompt_path.is_file():
        raise FileNotFoundError(
            f"System prompt not found: {prompt_path}\n"
            f"Expected at: {config.agent.system_prompt_file} relative to {config.client_dir}"
        )

    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read().strip()


def load_data_files(config: FrameworkConfig) -> dict[str, any]:
    """Load all business data files referenced in config.

    Returns:
        Dict mapping data key to loaded content.
        e.g. {"catalog": {...}, "shipping": {...}}

    Raises:
        FileNotFoundError: if a referenced data file doesn't exist
    """
    import json

    data = {}
    for ref in config.data_files:
        file_path = config.client_dir / "data" / ref.file
        if not file_path.is_file():
            raise FileNotFoundError(
                f"Data file not found: {file_path} (key: '{ref.key}')"
            )

        with open(file_path, "r", encoding="utf-8") as f:
            if ref.format == "json":
                data[ref.key] = json.load(f)
            elif ref.format == "yaml":
                data[ref.key] = yaml.safe_load(f)
            elif ref.format == "csv":
                import csv
                reader = csv.DictReader(f)
                data[ref.key] = list(reader)
            else:
                raise ValueError(f"Unsupported data format '{ref.format}' for key '{ref.key}'")

    return data


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _list_clients() -> list[str]:
    """List available client directories."""
    if not CLIENTS_DIR.is_dir():
        return []
    return sorted(
        d.name for d in CLIENTS_DIR.iterdir()
        if d.is_dir() and (d / DEFAULT_CONFIG_FILENAME).is_file()
    )


# ---------------------------------------------------------------------------
# CLI entrypoint for mini-run
# ---------------------------------------------------------------------------

def main():
    """CLI: python -m core.registry.loader --client <id>"""
    import argparse

    parser = argparse.ArgumentParser(description="Zuper Agent Framework — Config Loader")
    parser.add_argument("--client", type=str, help="Client ID (e.g. 'example')")
    parser.add_argument("--path", type=str, help="Direct path to config.yaml")
    parser.add_argument("--list", action="store_true", help="List available clients")
    args = parser.parse_args()

    if args.list:
        clients = _list_clients()
        if clients:
            print("Available clients:")
            for c in clients:
                print(f"  - {c}")
        else:
            print("No clients found in clients/ directory.")
        return

    try:
        config_path = Path(args.path) if args.path else None
        config = load_config(client_id=args.client, config_path=config_path)

        print("=" * 60)
        print("✅ Config loaded and validated successfully")
        print("=" * 60)
        print(f"  Client ID:       {config.client_id}")
        print(f"  Agent:           {config.agent.name}")
        print(f"  Model:           {config.agent.model}")
        print(f"  Fallback:        {config.agent.fallback_model}")
        print(f"  Client dir:      {config.client_dir}")
        print()
        print("  SDK Features:")
        print(f"    Sessions:      {'ON' if config.sdk.sessions.enabled else 'OFF'}")
        print(f"    Guardrails:    {'ON' if config.sdk.guardrails.enabled else 'OFF'}")
        print(f"    Handoffs:      {'ON' if config.sdk.handoffs.enabled else 'OFF'}")
        print()
        print("  Debug:")
        print(f"    Tracing:       {'ON' if config.debug.tracing.enabled else 'OFF'} → {config.debug.tracing.export_to}")
        print(f"    Log level:     {config.debug.logging.level.value}")
        print(f"    Log format:    {config.debug.logging.format}")
        print(f"    Dry run:       {config.debug.dry_run}")
        print()
        print("  Always ON:")
        print(f"    Buffer:        {config.session.buffer_timeout}s")
        print(f"    Anti-spam:     {config.session.antispam_ttl}s")
        print(f"    Lock:          {config.session.processing_lock_ttl}s")
        print(f"    Humanized:     {config.messaging.send_as_paragraphs}")
        print(f"    Filters:       groups={config.messaging.filter_groups} reactions={config.messaging.filter_reactions}")
        print()
        print("  Features:")
        print(f"    Human HITL:    {'ON' if config.human.enabled else 'OFF'}")
        print(f"    Habits:        {'ON' if config.habits.enabled else 'OFF'}")
        print(f"    Follow-up:     {'ON' if config.follow_up.enabled else 'OFF'}")
        print(f"    Media:         {'ON' if config.media.enabled else 'OFF'}")
        print(f"    Queue:         {'ON' if config.queue.enabled else 'OFF'}")
        print()
        print(f"  Tools:           {[t.module for t in config.tools] or '(none)'}")
        print(f"  Data files:      {[d.key for d in config.data_files] or '(none)'}")
        print(f"  Guardrail rules: {len(config.guardrails.input_patterns)} input, {len(config.guardrails.output_rules)} output")

        # Load system prompt
        prompt = load_system_prompt(config)
        print(f"\n  System prompt:   {len(prompt)} chars loaded from {config.agent.system_prompt_file}")
        print(f"  Preview:         {prompt[:80]}...")

        # Load data files
        data = load_data_files(config)
        if data:
            print(f"\n  Data loaded:     {list(data.keys())}")

        print("\n" + "=" * 60)

    except Exception as e:
        print(f"❌ Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
