"""
Smoke Bot — Code Tool
======================
Simple framework status tool to validate the tool loading pipeline.
"""

from __future__ import annotations

import sys
import platform
from agents import function_tool


@function_tool
def status_framework() -> str:
    """
    Returns the current Aleph Framework status and active features.
    Call this when the user asks about the framework status or wants to test tools.
    """
    return (
        "✅ Aleph Framework — smoke-test status\n"
        f"  Python:   {sys.version.split()[0]}\n"
        f"  Platform: {platform.system()} {platform.release()}\n"
        "  Features:\n"
        "    ✅ Code tool (this one)\n"
        "    ✅ Guardrails (redirect + inject + block)\n"
        "    ✅ Flows (onboarding + cep_lookup)\n"
        "    ✅ Webhook tool (buscar_cep via ViaCEP)\n"
        "    ✅ Data files (products.json)\n"
        "    ✅ Follow-up config\n"
        "    ⬜ Habits (disabled — no DB)\n"
        "    ⬜ Knowledge (disabled — no DB)\n"
        "    ⬜ Human HITL (disabled — no Z-API)\n"
    )
