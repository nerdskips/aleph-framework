"""
Aleph Framework — Flow Expression Evaluator
================================================
Safe, sandboxed evaluator for branch conditions and skip_if expressions.

**No eval(), no exec(), no ast.literal_eval() on arbitrary input.**

Supported grammar
-----------------
    lhs op rhs
    lhs is_empty
    lhs is_not_empty

lhs  ::= ["collected."] field_path
         field_path is a dot-separated sequence of identifiers.

op   ::= == | != | > | < | >= | <= | in | starts_with | ends_with

rhs  ::= "string" | 'string' | integer | float | [item, item, ...]
         list items may be quoted strings or numbers.

Examples
--------
    collected.type == 'A'
    customer.status != 'inactive'
    collected.age > 18
    collected.plan in ['basic', 'pro']
    collected.cpf is_not_empty
    answer starts_with 'sim'
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger("aleph.flows.expr")

# ---------------------------------------------------------------------------
# Tokeniser
# ---------------------------------------------------------------------------

# Matches a quoted string (single or double quotes, no escapes needed for our use)
_RE_QSTR = re.compile(r'^["\'](.+?)["\']$')
# Matches a list literal: ["a", "b"] or ['a', 'b'] or [1, 2]
_RE_LIST = re.compile(r"^\[(.+)\]$")
# Matches a number (int or float)
_RE_NUM = re.compile(r"^-?\d+(\.\d+)?$")


def _parse_value(token: str) -> object:
    """Parse a scalar or list rhs token into a Python value."""
    token = token.strip()
    # List
    m = _RE_LIST.match(token)
    if m:
        items = [_parse_value(item.strip()) for item in m.group(1).split(",")]
        return items
    # Quoted string
    m = _RE_QSTR.match(token)
    if m:
        return m.group(1)
    # Number
    if _RE_NUM.match(token):
        return float(token) if "." in token else int(token)
    # Bare word (treat as string)
    return token


def _resolve_lhs(path: str, collected: dict) -> object:
    """Resolve a dot-path like 'collected.customer.type' against *collected*."""
    parts = path.strip().split(".")
    # Strip optional "collected" prefix
    if parts and parts[0] == "collected":
        parts = parts[1:]
    val: object = collected
    for part in parts:
        if isinstance(val, dict):
            val = val.get(part)
            if val is None:
                return None
        else:
            return None
    return val


# ---------------------------------------------------------------------------
# Evaluator
# ---------------------------------------------------------------------------

# Pattern: <lhs> is_empty | is_not_empty
_RE_UNARY = re.compile(r"^(.+?)\s+(is_empty|is_not_empty)\s*$")

# Pattern: <lhs> <op> <rhs>
#   op group captures: ==, !=, >=, <=, >, <, in, starts_with, ends_with
_RE_BINARY = re.compile(
    r"^(.+?)\s+(==|!=|>=|<=|>|<|in|starts_with|ends_with)\s+(.+)$"
)


def evaluate(expr: str, collected: dict) -> bool:
    """Evaluate *expr* against *collected*. Returns False on any error.

    Never raises — malformed expressions are logged and return False.
    """
    expr = expr.strip()
    if not expr:
        return False

    # Unary operators
    m = _RE_UNARY.match(expr)
    if m:
        lhs_path, op = m.group(1), m.group(2)
        val = _resolve_lhs(lhs_path, collected)
        if op == "is_empty":
            return val is None or str(val).strip() == ""
        else:  # is_not_empty
            return val is not None and str(val).strip() != ""

    # Binary operators
    m = _RE_BINARY.match(expr)
    if m:
        lhs_path, op, rhs_token = m.group(1), m.group(2), m.group(3)
        lhs_val = _resolve_lhs(lhs_path, collected)
        rhs_val = _parse_value(rhs_token)

        try:
            if op == "==":
                return str(lhs_val) == str(rhs_val)
            if op == "!=":
                return str(lhs_val) != str(rhs_val)
            if op == "in":
                if isinstance(rhs_val, list):
                    return str(lhs_val) in [str(item) for item in rhs_val]
                return str(lhs_val) in str(rhs_val)
            if op == "starts_with":
                return str(lhs_val or "").startswith(str(rhs_val))
            if op == "ends_with":
                return str(lhs_val or "").endswith(str(rhs_val))
            # Numeric comparisons — coerce both sides
            lhs_num = float(lhs_val) if lhs_val is not None else 0.0
            rhs_num = float(rhs_val)
            if op == ">":
                return lhs_num > rhs_num
            if op == "<":
                return lhs_num < rhs_num
            if op == ">=":
                return lhs_num >= rhs_num
            if op == "<=":
                return lhs_num <= rhs_num
        except (TypeError, ValueError) as exc:
            logger.warning("Flow expr eval error for '%s': %s", expr, exc)
            return False

    logger.warning("Flow expr unrecognised pattern: '%s'", expr)
    return False
