"""
Zuper Agent Framework — Input Guardrails Engine
=================================================
Deterministic classification BEFORE the LLM call.
Zero cost, zero latency — pure regex/keyword matching.

Processes input_patterns from config.yaml:
  1. Normalizes text (lowercase, remove accents)
  2. Iterates patterns by priority (highest first)
  3. Tests keywords then regex
  4. Returns ClassificationResult with action, tool_choice, etc.

If no pattern matches → action=CONTINUE (go to LLM).
"""

from __future__ import annotations

import re
import unicodedata
import logging
from dataclasses import dataclass, field

from core.registry.schema import GuardrailAction, GuardrailsConfig, InputPattern

logger = logging.getLogger("zuper.guardrails")


# ---------------------------------------------------------------------------
# Classification result
# ---------------------------------------------------------------------------

@dataclass
class ClassificationResult:
    """Result of input guardrail classification."""
    matched: bool = False
    pattern_name: str = ""
    action: GuardrailAction = GuardrailAction.CONTINUE
    tool_choice: str = "auto"
    inject_instruction: str = ""
    redirect_message: str = ""
    original_text: str = ""
    normalized_text: str = ""


# ---------------------------------------------------------------------------
# Text normalization
# ---------------------------------------------------------------------------

def normalize_text(text: str, remove_accents: bool = True, lowercase: bool = True) -> str:
    """Normalize text for pattern matching."""
    if lowercase:
        text = text.lower()
    if remove_accents:
        nfkd = unicodedata.normalize("NFKD", text)
        text = "".join(c for c in nfkd if not unicodedata.combining(c))
    return text.strip()


# ---------------------------------------------------------------------------
# Matching engine
# ---------------------------------------------------------------------------

def _match_keywords(text: str, keywords: list[str]) -> bool:
    """Check if any keyword appears in the text."""
    for kw in keywords:
        if kw in text:
            return True
    return False


def _match_regex(text: str, patterns: list[str]) -> bool:
    """Check if any regex pattern matches the text."""
    for pattern in patterns:
        try:
            if re.search(pattern, text):
                return True
        except re.error:
            logger.warning("Invalid regex in guardrail: %s", pattern)
    return False


def _match_pattern(text: str, pattern: InputPattern) -> bool:
    """Check if a single pattern matches the text."""
    if pattern.keywords and _match_keywords(text, pattern.keywords):
        return True
    if pattern.regex and _match_regex(text, pattern.regex):
        return True
    return False


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def classify_input(text: str, config: GuardrailsConfig) -> ClassificationResult:
    """Classify user input against configured guardrail patterns.

    Args:
        text: Raw user message
        config: GuardrailsConfig from the client's YAML

    Returns:
        ClassificationResult with matched pattern and action.
        If no match, action=CONTINUE (proceed to LLM).
    """
    # Normalize
    normalized = normalize_text(
        text,
        remove_accents=config.normalize_accents,
        lowercase=config.normalize_lowercase,
    )

    result = ClassificationResult(
        original_text=text,
        normalized_text=normalized,
    )

    if not config.input_patterns:
        return result

    # Sort by priority (highest first)
    sorted_patterns = sorted(config.input_patterns, key=lambda p: p.priority, reverse=True)

    for pattern in sorted_patterns:
        if _match_pattern(normalized, pattern):
            result.matched = True
            result.pattern_name = pattern.name
            result.action = pattern.action
            result.tool_choice = pattern.tool_choice
            result.inject_instruction = pattern.inject_instruction
            result.redirect_message = pattern.redirect_message

            logger.info(
                "Input guardrail matched: pattern=%s action=%s text='%s'",
                pattern.name, pattern.action.value, normalized[:80],
            )
            return result

    logger.debug("No input guardrail matched for: '%s'", normalized[:80])
    return result
