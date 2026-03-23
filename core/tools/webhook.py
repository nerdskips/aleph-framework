"""
Zuper Agent Framework — Webhook Tool Generator
=================================================
Generates SDK @function_tool instances dynamically from YAML webhook
definitions. The LLM sees a normal tool with name, description, and
typed parameters. When called, the framework POSTs to the webhook URL
and returns the JSON response.

The junior writes YAML, never Python:

  tools:
    - name: "consultar_assinatura"
      type: "webhook"
      description: "Consulta assinatura do cliente por telefone"
      webhook_url: "http://n8n:5678/webhook/sheets"
      parameters:
        acao:
          type: "string"
          default: "consultar"
          hidden: true
        phone:
          type: "string"
          description: "Telefone do cliente"

Contract:
  Request:  JSON body with all parameters → webhook
  Response: JSON body from webhook → returned to LLM as string
"""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from agents import function_tool
from core.registry.schema import ToolRef, ToolType, WebhookParam

logger = logging.getLogger("zuper.tools")


# ---------------------------------------------------------------------------
# Type mapping (YAML type → Python annotation string for the SDK)
# ---------------------------------------------------------------------------

TYPE_MAP = {
    "string": str,
    "number": float,
    "integer": int,
    "boolean": bool,
}


# ---------------------------------------------------------------------------
# Webhook tool generator
# ---------------------------------------------------------------------------

def generate_webhook_tool(tool_ref: ToolRef) -> Any:
    """Generate a @function_tool from a YAML webhook definition.

    Creates a Python function dynamically with:
      - Name and docstring from YAML (what the LLM sees)
      - Parameters from YAML (typed, with descriptions)
      - Hidden parameters injected automatically
      - HTTP POST/GET to webhook_url on call
      - JSON response returned as string to LLM

    Args:
        tool_ref: ToolRef with type='webhook' from config.yaml

    Returns:
        A FunctionTool instance ready for Agent(tools=[...])
    """
    # Separate visible params (LLM provides) from hidden params (auto-injected)
    visible_params: dict[str, WebhookParam] = {}
    hidden_params: dict[str, Any] = {}

    for param_name, param_config in tool_ref.parameters.items():
        if param_config.hidden:
            hidden_params[param_name] = param_config.default
        else:
            visible_params[param_name] = param_config

    # Build the function dynamically
    # We use a closure to capture tool_ref and hidden_params
    webhook_url = tool_ref.webhook_url
    method = tool_ref.method.upper()
    timeout = tool_ref.timeout_seconds
    tool_name = tool_ref.name
    tool_returns = tool_ref.returns

    async def webhook_caller(**kwargs) -> str:
        """Dynamic webhook tool — calls N8N/external webhook."""
        # Build payload: hidden params + LLM-provided params
        payload = dict(hidden_params)
        payload.update(kwargs)

        logger.info(
            "Webhook tool '%s' calling %s %s with %s",
            tool_name, method, webhook_url, list(payload.keys()),
        )

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                if method == "GET":
                    response = await client.get(webhook_url, params=payload)
                else:
                    response = await client.post(webhook_url, json=payload)

                response.raise_for_status()

                try:
                    data = response.json()
                except Exception:
                    data = {"raw": response.text}

            logger.info(
                "Webhook tool '%s' responded: %d bytes",
                tool_name, len(response.text),
            )

            # Return as formatted JSON string for the LLM
            return json.dumps(data, ensure_ascii=False, indent=2)

        except httpx.TimeoutException:
            error = f"Timeout ao chamar {tool_name} ({timeout}s)"
            logger.error(error)
            return json.dumps({"error": error})

        except httpx.HTTPStatusError as e:
            error = f"Erro HTTP {e.response.status_code} ao chamar {tool_name}"
            logger.error("%s: %s", error, str(e)[:200])
            return json.dumps({"error": error})

        except Exception as e:
            error = f"Erro ao chamar {tool_name}: {str(e)[:200]}"
            logger.error(error)
            return json.dumps({"error": error})

    # Build the docstring (what the LLM sees)
    doc_lines = [tool_ref.description or f"Calls {tool_name}"]
    if tool_returns:
        doc_lines.append(f"\nReturns: {tool_returns}")
    doc_lines.append("\nArgs:")
    for param_name, param_config in visible_params.items():
        p_desc = param_config.description or param_name
        p_type = param_config.type
        if param_config.enum:
            p_desc += f" (options: {', '.join(param_config.enum)})"
        doc_lines.append(f"    {param_name} ({p_type}): {p_desc}")

    webhook_caller.__name__ = tool_name
    webhook_caller.__qualname__ = tool_name
    webhook_caller.__doc__ = "\n".join(doc_lines)

    # Build annotations for the SDK's schema generator
    annotations = {}
    for param_name, param_config in visible_params.items():
        py_type = TYPE_MAP.get(param_config.type, str)
        if not param_config.required and param_config.default is not None:
            # Optional with default — SDK handles this
            annotations[param_name] = py_type
        else:
            annotations[param_name] = py_type

    webhook_caller.__annotations__ = annotations
    webhook_caller.__annotations__["return"] = str

    # Set defaults for optional params
    defaults = {}
    for param_name, param_config in visible_params.items():
        if not param_config.required and param_config.default is not None:
            defaults[param_name] = param_config.default

    if defaults:
        webhook_caller.__kwdefaults__ = defaults

    # Wrap with @function_tool
    tool = function_tool(webhook_caller, name_override=tool_name)

    logger.info(
        "Webhook tool generated: name=%s url=%s params=%s hidden=%s",
        tool_name, webhook_url,
        list(visible_params.keys()),
        list(hidden_params.keys()),
    )

    return tool


def generate_webhook_tools(tool_refs: list[ToolRef]) -> list[Any]:
    """Generate all webhook tools from a list of ToolRefs.

    Filters to only type='webhook' entries.

    Returns:
        List of FunctionTool instances
    """
    tools = []
    for ref in tool_refs:
        if ref.type == ToolType.WEBHOOK:
            tool = generate_webhook_tool(ref)
            tools.append(tool)
    return tools
