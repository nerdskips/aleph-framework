---
name: aleph-schema
description: Create or modify Pydantic schema classes for the Aleph Framework. Use when adding new config sections to schema.py, creating new features that need YAML configuration, or modifying existing FrameworkConfig fields. Triggers on schema.py changes, new feature configs, or YAML field additions.
---

# Aleph Schema — Pydantic Config Patterns

When creating or modifying schema classes in `core/registry/schema.py`, follow these patterns exactly.

## New Feature Config Class

```python
class MyFeatureConfig(BaseModel):
    """Description of feature.
    DEFAULT OFF — enable when [use case].
    """
    enabled: bool = Field(False)
    # All fields must have Field() with description
    my_setting: str = Field("default_value", description="What this setting does")
    my_number: int = Field(10, ge=1, le=100, description="Range-validated number")
```

## Adding to FrameworkConfig

Always add new features in the correct section of FrameworkConfig, following alphabetical order within the feature block:

```python
    my_feature: MyFeatureConfig = Field(default_factory=MyFeatureConfig)
```

## Rules

1. New features are ALWAYS `enabled: bool = Field(False)` — DEFAULT OFF
2. Every Field() MUST have a `description` parameter
3. Use `Field(default_factory=...)` for complex types, not `Field(default=...)`
4. Avoid field names that shadow BaseModel attributes (e.g., don't use `schema` — use `db_schema` with alias)
5. Import `from __future__ import annotations` MUST be first after docstring
6. Validators use `@model_validator(mode="after")` or `@field_validator`
7. `client_dir` property checks `AGENT_DIR` env var first, then falls back to `clients/<id>/`

## Existing Patterns to Reference

- `HabitsConfig` (line ~294) — database feature with auto_migrate
- `KnowledgeConfig` (line ~316) — RAG feature with search params
- `GuardrailsConfig` — has both bool flags and list fields
- `HumanConfig` — complex config with TTLs and phone lists