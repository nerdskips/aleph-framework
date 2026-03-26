"""
Aleph Framework — Output Guardrails Engine
==================================================
Post-LLM validation. Checks agent response for:
  - Fabricated information (addresses, internal data, fake branches)
  - Price leaks (loose prices outside budget context)
  - Ghost escalation (LLM claims escalation without calling tool)
  - Custom regex rules from YAML

Runs AFTER the LLM responds, BEFORE sending to user.
If a violation is detected, the response is replaced with a safe message.
"""

from __future__ import annotations

import re
import logging
from dataclasses import dataclass

from core.registry.schema import (
    GuardrailsConfig,
    OutputGuardrailType,
    OutputGuardrailRule,
)

logger = logging.getLogger("aleph.guardrails")


# ---------------------------------------------------------------------------
# Built-in patterns (from Laura production — generic enough for any agent)
# ---------------------------------------------------------------------------

FABRICATION_PATTERNS = [
    r"(?:rua|avenida|av\.)\s+[A-Z][a-záéíóúãõ]+",       # fake address
    r"(?:fundad[oa]|desde)\s+(?:em\s+)?\d{4}",            # fake founding date
    r"(?:cnpj|cpf)\s*[:\s]\s*\d",                         # fake document
    r"(?:proprietári[oa]|don[oa])\s+(?:é|se chama)\s+\w+", # fake owner
    r"(?:unidade|filial|loja)\s+(?:da|de|do|na|no|em)\s+[A-Z]",  # fake branch
]

PRICE_LEAK_PATTERNS = [
    r"R\$\s*\d+",  # any R$ amount
]

# Prices that are OK (shipping rates, etc) — won't trigger price leak
PRICE_EXEMPT_PATTERNS = [
    r"(?:frete|entrega|taxa).*R\$\s*\d+",
    r"R\$\s*(?:10|15|20|25)(?:[,.]00)?",  # common shipping rates
]

GHOST_ESCALATION_PATTERNS = [
    r"(?:vou\s+(?:consultar|verificar|perguntar|checar))",
    r"(?:vou\s+(?:encaminhar|transferir|repassar))",
    r"(?:(?:entr|entrar)(?:ei|o)\s+em\s+contato\s+com\s+(?:a\s+)?equipe)",
    r"(?:(?:já|vou)\s+(?:acionar|avisar|notificar))",
]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class OutputGuardrailResult:
    """Result of output guardrail check."""
    blocked: bool = False
    rule_name: str = ""
    rule_type: str = ""
    safe_response: str = ""
    original_response: str = ""


# ---------------------------------------------------------------------------
# Built-in checks
# ---------------------------------------------------------------------------

def _check_fabrication(text: str) -> bool:
    """Check for fabricated information."""
    text_lower = text.lower()
    for pattern in FABRICATION_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True
    return False


def _check_price_leak(text: str, intent: str = "") -> bool:
    """Check for loose prices outside budget context.
    Exempts known-safe patterns (shipping rates)."""
    # Skip if intent is budget/order related
    exempt_intents = {"orcamento", "pedido_avulso", "pedido_avulso_continuacao"}
    if intent in exempt_intents:
        return False

    # Check for any price
    has_price = bool(re.search(r"R\$\s*\d+", text))
    if not has_price:
        return False

    # Check if it matches an exempt pattern (shipping, etc)
    for pattern in PRICE_EXEMPT_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return False

    return True


def _check_ghost_escalation(text: str, tool_calls: list[str] | None = None) -> bool:
    """Check if LLM claims escalation without actually calling the tool."""
    text_lower = text.lower()

    # Check if response contains escalation language
    has_escalation_language = False
    for pattern in GHOST_ESCALATION_PATTERNS:
        if re.search(pattern, text_lower):
            has_escalation_language = True
            break

    if not has_escalation_language:
        return False

    # Check if escalation tool was actually called
    if tool_calls:
        escalation_tools = {"escalonar_humano", "escalate", "escalonar"}
        if any(tc in escalation_tools for tc in tool_calls):
            return False  # Tool was called, not ghost

    # Escalation language without tool call = ghost
    return True


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def check_output(
    response: str,
    config: GuardrailsConfig,
    intent: str = "",
    tool_calls: list[str] | None = None,
) -> OutputGuardrailResult:
    """Check agent response against output guardrails.

    Args:
        response: Agent's text response
        config: GuardrailsConfig from YAML
        intent: Detected input intent (for exemptions)
        tool_calls: List of tool names called during this run

    Returns:
        OutputGuardrailResult. If blocked=True, use safe_response instead.
    """
    result = OutputGuardrailResult(original_response=response)
    default_safe = "Desculpe, não tenho essa informação. Posso verificar com a equipe?"

    # If tools were called this turn, the data came from tools — not fabricated.
    # Skip fabrication and price leak guards (they're meant to catch LLM hallucination).
    tools_were_called = bool(tool_calls)

    # --- Built-in: fabrication ---
    if config.enable_fabrication_guard and not tools_were_called and _check_fabrication(response):
        result.blocked = True
        result.rule_name = "fabrication_builtin"
        result.rule_type = "fabrication"
        result.safe_response = default_safe
        logger.warning("Output blocked [fabrication]: %s", response[:100])
        return result

    # --- Built-in: price leak ---
    if config.enable_price_leak_guard and not tools_were_called and _check_price_leak(response, intent):
        result.blocked = True
        result.rule_name = "price_leak_builtin"
        result.rule_type = "price_leak"
        result.safe_response = default_safe
        logger.warning("Output blocked [price_leak]: %s", response[:100])
        return result

    # --- Built-in: ghost escalation ---
    if config.enable_ghost_escalation_guard and _check_ghost_escalation(response, tool_calls):
        result.blocked = True
        result.rule_name = "ghost_escalation_builtin"
        result.rule_type = "ghost_escalation"
        result.safe_response = default_safe
        logger.warning("Output blocked [ghost_escalation]: %s", response[:100])
        return result

    # --- Custom YAML rules ---
    for rule in config.output_rules:
        if not rule.enabled:
            continue

        # Check intent exemptions
        if intent and intent in rule.exempt_intents:
            continue

        # Match patterns
        for pattern in rule.patterns:
            try:
                if re.search(pattern, response, re.IGNORECASE):
                    result.blocked = True
                    result.rule_name = rule.name
                    result.rule_type = rule.type.value
                    result.safe_response = rule.safe_response
                    logger.warning(
                        "Output blocked [%s/%s]: %s",
                        rule.type.value, rule.name, response[:100],
                    )
                    return result
            except re.error:
                logger.warning("Invalid regex in output rule '%s': %s", rule.name, pattern)

    return result
