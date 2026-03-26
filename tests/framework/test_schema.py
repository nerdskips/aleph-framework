"""Test: schema.py validates config.yaml correctly — v2 with SDK + debug."""

import yaml
import sys
sys.path.insert(0, "/root/zuper-framework")

from core.registry.schema import FrameworkConfig, LogLevel


def test_example_config():
    """Load the example config.yaml and validate all defaults."""
    with open("/root/zuper-framework/clients/example/config.yaml") as f:
        raw = yaml.safe_load(f)

    config = FrameworkConfig(**raw)

    # Required fields
    assert config.client_id == "example"
    assert config.agent.name == "Echo Bot"
    assert config.agent.model == "openai/gpt-4.1-mini"
    assert config.agent.fallback_model == "google/gemini-2.5-flash"

    # --- SDK 0.12 defaults ---
    # Sessions: DEFAULT ON
    assert config.sdk.sessions.enabled is True
    assert config.sdk.sessions.history_limit == 50
    assert config.sdk.sessions.ttl == 10800
    assert config.sdk.sessions.redis_key_prefix == "zuper:session:"
    # Guardrails: DEFAULT ON
    assert config.sdk.guardrails.enabled is True
    assert config.sdk.guardrails.run_input_guardrails is True
    assert config.sdk.guardrails.run_output_guardrails is True
    assert config.sdk.guardrails.run_tool_guardrails is True
    # Handoffs: DEFAULT OFF
    assert config.sdk.handoffs.enabled is False
    assert config.sdk.handoffs.max_turns == 15

    # --- Debug & Observability defaults ---
    # Tracing: ALWAYS ON
    assert config.debug.tracing.enabled is True
    assert config.debug.tracing.export_to == "console"
    assert config.debug.tracing.include_tool_calls is True
    assert config.debug.tracing.include_model_io is True
    assert config.debug.tracing.sample_rate == 1.0
    # Logging: ALWAYS ON
    assert config.debug.logging.level == LogLevel.INFO
    assert config.debug.logging.format == "json"
    assert config.debug.logging.include_request_id is True
    assert config.debug.logging.log_guardrail_decisions is True
    assert config.debug.logging.log_tool_calls is True
    assert config.debug.logging.log_llm_latency is True
    assert config.debug.logging.log_session_events is True
    assert config.debug.logging.log_human_events is True
    assert config.debug.logging.log_buffer_events is True
    # Debug flags
    assert config.debug.verbose_errors is True
    assert config.debug.dry_run is False

    # --- ALWAYS ON: Session (WhatsApp quirks) ---
    assert config.session.buffer_timeout == 8
    assert config.session.antispam_ttl == 120
    assert config.session.processing_lock_ttl == 30
    assert config.session.context_ttl == 10800

    # --- ALWAYS ON: Guardrails (deterministic) ---
    assert config.guardrails.normalize_accents is True
    assert config.guardrails.normalize_lowercase is True
    assert config.guardrails.enable_fabrication_guard is True
    assert config.guardrails.enable_price_leak_guard is True
    assert config.guardrails.enable_ghost_escalation_guard is True

    # --- ALWAYS ON: Messaging ---
    assert config.messaging.send_as_paragraphs is True
    assert config.messaging.delay_min_ms == 800
    assert config.messaging.delay_max_ms == 2500
    assert config.messaging.filter_groups is True
    assert config.messaging.filter_newsletters is True
    assert config.messaging.filter_broadcasts is True
    assert config.messaging.filter_reactions is True
    assert config.messaging.filter_edits is True
    assert config.messaging.disclaimer.text == "Echo Bot — Aleph Framework test"

    # --- ALWAYS ON: LLM ---
    assert config.llm.gateway_url == "http://bifrost:8080/v1"
    assert config.llm.timeout_seconds == 60
    assert config.llm.fallback_timeout_seconds == 120

    # --- Explicitly disabled for echo bot ---
    assert config.human.enabled is False

    # --- DEFAULT OFF ---
    assert config.habits.enabled is False
    assert config.follow_up.enabled is False
    assert config.media.enabled is False
    assert config.queue.enabled is False

    # Tools
    assert len(config.tools) == 1
    assert config.tools[0].module == "echo_tools"

    # Client dir
    from pathlib import Path
    assert config.client_dir == Path("clients/example")

    print("✅ All assertions passed — schema v2 validates correctly")
    print(f"   Client: {config.client_id}")
    print(f"   Agent: {config.agent.name} ({config.agent.model})")
    print(f"   SDK Sessions: {config.sdk.sessions.enabled}")
    print(f"   SDK Guardrails: {config.sdk.guardrails.enabled}")
    print(f"   SDK Handoffs: {config.sdk.handoffs.enabled}")
    print(f"   Tracing: {config.debug.tracing.enabled} → {config.debug.tracing.export_to}")
    print(f"   Log level: {config.debug.logging.level.value}")
    print(f"   Dry run: {config.debug.dry_run}")
    print(f"   Buffer: {config.session.buffer_timeout}s")
    print(f"   Tools: {[t.module for t in config.tools]}")


def test_missing_required_field():
    """Pydantic rejects config without 'agent'."""
    try:
        FrameworkConfig(client_id="test")
        print("❌ Should have raised ValidationError")
    except Exception as e:
        assert "agent" in str(e).lower()
        print("✅ Missing 'agent' correctly rejected")


def test_invalid_regex():
    """Pydantic rejects invalid regex in guardrail patterns."""
    raw = {
        "client_id": "test",
        "agent": {"name": "Test"},
        "guardrails": {
            "input_patterns": [
                {"name": "bad", "regex": ["[invalid("]}
            ]
        }
    }
    try:
        FrameworkConfig(**raw)
        print("❌ Should have raised ValidationError for bad regex")
    except Exception as e:
        assert "regex" in str(e).lower() or "invalid" in str(e).lower()
        print("✅ Invalid regex correctly rejected")


def test_invalid_temperature():
    """Pydantic rejects temperature > 2.0."""
    raw = {
        "client_id": "test",
        "agent": {"name": "Test", "temperature": 5.0}
    }
    try:
        FrameworkConfig(**raw)
        print("❌ Should have raised ValidationError")
    except Exception:
        print("✅ Temperature > 2.0 correctly rejected")


def test_debug_override():
    """Client can override debug settings via YAML."""
    raw = {
        "client_id": "test",
        "agent": {"name": "Test"},
        "debug": {
            "tracing": {
                "enabled": True,
                "export_to": "braintrust",
                "sample_rate": 0.5,
                "include_model_io": False,
            },
            "logging": {
                "level": "DEBUG",
                "format": "text",
                "log_to_file": "/var/log/zuper/test.log",
            },
            "verbose_errors": False,
            "dry_run": True,
        }
    }
    config = FrameworkConfig(**raw)
    assert config.debug.tracing.export_to == "braintrust"
    assert config.debug.tracing.sample_rate == 0.5
    assert config.debug.tracing.include_model_io is False
    assert config.debug.logging.level == LogLevel.DEBUG
    assert config.debug.logging.format == "text"
    assert config.debug.logging.log_to_file == "/var/log/zuper/test.log"
    assert config.debug.verbose_errors is False
    assert config.debug.dry_run is True
    print("✅ Debug overrides work correctly")


def test_sdk_handoffs_enabled():
    """Client can enable multi-agent handoffs."""
    raw = {
        "client_id": "test",
        "agent": {"name": "Test"},
        "sdk": {
            "handoffs": {
                "enabled": True,
                "mode": "manager",
                "max_turns": 25,
            }
        }
    }
    config = FrameworkConfig(**raw)
    assert config.sdk.handoffs.enabled is True
    assert config.sdk.handoffs.mode.value == "manager"
    assert config.sdk.handoffs.max_turns == 25
    print("✅ SDK handoffs config works correctly")


def test_full_agent_config():
    """Simulate a real production agent with most features enabled."""
    raw = {
        "client_id": "padaria-test",
        "agent": {
            "name": "Laura",
            "description": "Agente Le Crocant",
            "model": "openai/gpt-4.1-mini",
            "fallback_model": "google/gemini-2.5-flash",
            "temperature": 0.6,
            "max_tokens": 2048,
        },
        "sdk": {
            "handoffs": {"enabled": True, "mode": "peer"},
        },
        "debug": {
            "tracing": {"export_to": "braintrust"},
            "logging": {"level": "DEBUG"},
        },
        "guardrails": {
            "input_patterns": [
                {
                    "name": "greeting",
                    "keywords": ["oi", "bom dia", "boa tarde", "boa noite"],
                    "action": "continue",
                },
                {
                    "name": "escalation",
                    "keywords": ["cancelar", "falar com humano"],
                    "action": "escalate",
                    "tool_choice": "required",
                },
            ],
            "output_rules": [
                {
                    "name": "no_fake_address",
                    "type": "fabrication",
                    "patterns": ["(?:rua|avenida|av\\.)\\s+[A-Z]"],
                }
            ],
        },
        "human": {
            "enabled": True,
            "responsible_phones": ["5534999999999"],
        },
        "habits": {
            "enabled": True,
            "dedup_threshold": 0.025,
        },
        "follow_up": {
            "enabled": True,
            "steps": [
                {"delay_minutes": 10, "message": "Oi {name}, posso ajudar com mais algo?"},
                {"delay_minutes": 30, "message": "Ainda estou aqui se precisar!"},
            ]
        },
        "media": {
            "enabled": True,
            "supported_types": ["audio", "image"],
        },
        "tools": [
            {"module": "sheets", "functions": ["consultar_assinatura", "registrar_pedido"]},
            {"module": "cardapio"},
        ],
        "data_files": [
            {"key": "catalog", "file": "cardapio.json"},
            {"key": "shipping", "file": "frete.json"},
        ],
    }
    config = FrameworkConfig(**raw)
    assert config.client_id == "padaria-test"
    assert config.agent.name == "Laura"
    assert config.habits.enabled is True
    assert config.habits.dedup_threshold == 0.025
    assert config.follow_up.enabled is True
    assert len(config.follow_up.steps) == 2
    assert config.media.enabled is True
    assert len(config.media.supported_types) == 2
    assert len(config.guardrails.input_patterns) == 2
    assert len(config.guardrails.output_rules) == 1
    assert len(config.tools) == 2
    assert len(config.data_files) == 2
    assert config.sdk.handoffs.enabled is True
    assert config.debug.tracing.export_to == "braintrust"
    assert config.debug.logging.level == LogLevel.DEBUG
    # ALWAYS ON defaults still applied
    assert config.session.buffer_timeout == 8
    assert config.messaging.filter_groups is True
    assert config.guardrails.enable_fabrication_guard is True
    print("✅ Full production agent config validates correctly")


if __name__ == "__main__":
    test_example_config()
    test_missing_required_field()
    test_invalid_regex()
    test_invalid_temperature()
    test_debug_override()
    test_sdk_handoffs_enabled()
    test_full_agent_config()
    print("\n🎉 All schema v2 tests passed!")
