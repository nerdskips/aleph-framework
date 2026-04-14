"""
Aleph Framework — Flow Template Engine
==========================================
Simple {{ collected.field }} interpolation for flow step messages,
webhook URLs, and payload values.

No external dependencies — regex-based replacement over a restricted namespace.
Missing keys resolve to empty string (never raise).
"""

from __future__ import annotations

import re

# Matches {{ collected.field }} and {{ collected.nested.field }}
_TEMPLATE_RE = re.compile(r"\{\{\s*([\w.]+)\s*\}\}")


def render(template: str, collected: dict) -> str:
    """Interpolate ``{{ collected.field }}`` patterns in *template*.

    Supports nested paths: ``{{ collected.customer.name }}``.
    The ``collected.`` prefix is optional — ``{{ name }}`` also resolves
    against the collected dict.

    Missing keys → empty string. Non-string values are converted with ``str()``.

    Args:
        template: String that may contain ``{{ ... }}`` placeholders.
        collected: The flow's collected-data dict.

    Returns:
        String with placeholders replaced by their values.
    """
    if "{{" not in template:
        return template

    def _replace(match: re.Match) -> str:
        key_path = match.group(1).strip()
        parts = key_path.split(".")
        # Strip optional "collected" prefix
        if parts and parts[0] == "collected":
            parts = parts[1:]
        val: object = collected
        for part in parts:
            if isinstance(val, dict):
                val = val.get(part)
                if val is None:
                    return ""
            else:
                return ""
        return str(val) if val is not None else ""

    return _TEMPLATE_RE.sub(_replace, template)


def render_dict(payload: dict, collected: dict) -> dict:
    """Apply :func:`render` to every string value in *payload* (shallow)."""
    return {
        k: render(v, collected) if isinstance(v, str) else v
        for k, v in payload.items()
    }
