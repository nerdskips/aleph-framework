"""Test: schema.py validates config.yaml correctly."""

import yaml
import sys
sys.path.insert(0, "/home/claude/zuper-framework")

from core.registry.schema import FrameworkConfig


def test_example_config():
    """Load the example config.yaml and validate it."""
    with open("/home/claude/zuper-framework/clients/example/config.yaml") as f:
        raw = yaml.safe_load(f)

    config = FrameworkConfig(**raw)

    # Required fields
    assert config.client_id == "example"
    assert config.agent.name == "Echo Bot"
    assert config.agent.model == "openai/gpt-4.1-mini"

    # Defaults populated
    assert config.session.buffer_timeout == 8
    assert config.session.history_ttl == 10800
    assert config.session.history_max == 50
    assert config.session.antispam_ttl == 120

    # Explicitly disabled
    assert config.human.enabled is False
    assert config.habits.enabled is False
    assert config.follow_up.enabled is False
    assert config.media.enabled is False
    assert config.queue.enabled is False

    # Messaging defaults
    assert config.messaging.send_as_paragraphs is True
    assert config.messaging.filter_groups is True
    assert config.messaging.disclaimer.text == "Echo Bot — Zuper Agent Framework test"

    # Tools
    assert len(config.tools) == 1
    assert config.tools[0].module == "echo_tools"

    # LLM defaults
    assert config.llm.gateway_url == "http://bifrost:8080/v1"
    assert config.llm.timeout_seconds == 60

    # Client dir
    from pathlib import Path
    assert config.client_dir == Path("clients/example")

    print("✅ All assertions passed — schema validates correctly")
    print(f"   Client: {config.client_id}")
    print(f"   Agent: {config.agent.name}")
    print(f"   Model: {config.agent.model}")
    print(f"   Buffer: {config.session.buffer_timeout}s")
    print(f"   History TTL: {config.session.history_ttl}s")
    print(f"   Tools: {[t.module for t in config.tools]}")


def test_missing_required_field():
    """Verify Pydantic rejects config without required fields."""
    try:
        FrameworkConfig(client_id="test")
        print("❌ Should have raised ValidationError")
    except Exception as e:
        assert "agent" in str(e).lower()
        print("✅ Missing 'agent' correctly rejected")


def test_invalid_regex():
    """Verify Pydantic rejects invalid regex in guardrail patterns."""
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
    """Verify Pydantic rejects out-of-range temperature."""
    raw = {
        "client_id": "test",
        "agent": {"name": "Test", "temperature": 5.0}
    }
    try:
        FrameworkConfig(**raw)
        print("❌ Should have raised ValidationError for temperature > 2.0")
    except Exception:
        print("✅ Temperature > 2.0 correctly rejected")


if __name__ == "__main__":
    test_example_config()
    test_missing_required_field()
    test_invalid_regex()
    test_invalid_temperature()
    print("\n🎉 All schema tests passed!")
