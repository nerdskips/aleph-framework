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
from core.registry.schema import ToolRef, ToolType, WebhookParam, ParamsIn

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

    Creates a proper typed Python function that the SDK can introspect
    for JSON schema generation. Uses exec() to build a function with
    explicit typed parameters instead of **kwargs.

    Supports 4 params_in modes:
      - body:  all params sent as JSON body (N8N pattern)
      - query: all params sent as ?key=val query string
      - url:   all params resolved as {param} in URL
      - auto:  {param} in URL resolved first, rest → body (POST) or query (GET)

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

    # Capture closure vars
    webhook_url = tool_ref.webhook_url
    method = tool_ref.method.upper()
    timeout = tool_ref.timeout_seconds
    tool_name = tool_ref.name
    tool_returns = tool_ref.returns
    params_in = tool_ref.params_in

    # Build the actual async caller (captures everything via closure)
    async def _do_call(params: dict) -> str:
        all_params = dict(hidden_params)
        all_params.update(params)

        # --- Resolve URL placeholders ({param} in URL) ---
        resolved_url = webhook_url
        url_params_used = set()

        # Always resolve {param} placeholders in URL regardless of params_in mode
        for key, value in all_params.items():
            placeholder = "{" + key + "}"
            if placeholder in resolved_url:
                resolved_url = resolved_url.replace(placeholder, str(value))
                url_params_used.add(key)

        # Remaining params (not consumed by URL template)
        remaining = {k: v for k, v in all_params.items() if k not in url_params_used}

        # --- Determine where remaining params go ---
        send_as_body = None
        send_as_query = None

        if params_in == ParamsIn.URL:
            # URL-only mode: everything should be in URL, nothing extra
            pass
        elif params_in == ParamsIn.BODY:
            send_as_body = remaining if remaining else None
        elif params_in == ParamsIn.QUERY:
            send_as_query = remaining if remaining else None
        elif params_in == ParamsIn.AUTO:
            # Auto: POST → body, GET → query
            if method == "GET":
                send_as_query = remaining if remaining else None
            else:
                send_as_body = remaining if remaining else None

        logger.info(
            "Webhook tool '%s' → %s %s | body=%s query=%s url_resolved=%s",
            tool_name, method, resolved_url,
            list(send_as_body.keys()) if send_as_body else "-",
            list(send_as_query.keys()) if send_as_query else "-",
            list(url_params_used) if url_params_used else "-",
        )

        try:
            async with httpx.AsyncClient(timeout=float(timeout)) as client:
                if method == "GET":
                    response = await client.get(resolved_url, params=send_as_query)
                else:
                    response = await client.post(
                        resolved_url,
                        json=send_as_body,
                        params=send_as_query,
                    )

                response.raise_for_status()

                try:
                    data = response.json()
                except Exception:
                    data = {"raw": response.text}

            logger.info(
                "Webhook tool '%s' responded: %d bytes",
                tool_name, len(response.text),
            )
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

    # Build docstring
    doc_lines = [tool_ref.description or f"Calls {tool_name}"]
    if tool_returns:
        doc_lines.append(f"\nReturns: {tool_returns}")
    if visible_params:
        doc_lines.append("\nArgs:")
        for param_name, param_config in visible_params.items():
            p_desc = param_config.description or param_name
            if param_config.enum:
                p_desc += f" (options: {', '.join(param_config.enum)})"
            doc_lines.append(f"    {param_name}: {p_desc}")

    docstring = "\n".join(doc_lines)

    # Build a proper function with explicit typed params using exec()
    # This gives the SDK clean introspectable parameters for schema generation
    param_parts = []
    for param_name, param_config in visible_params.items():
        py_type_name = {
            "string": "str",
            "number": "float",
            "integer": "int",
            "boolean": "bool",
        }.get(param_config.type, "str")

        if not param_config.required and param_config.default is not None:
            default_repr = repr(param_config.default)
            param_parts.append(f"{param_name}: {py_type_name} = {default_repr}")
        else:
            param_parts.append(f"{param_name}: {py_type_name}")

    params_str = ", ".join(param_parts)
    param_names = list(visible_params.keys())
    collect_str = ", ".join(f'"{p}": {p}' for p in param_names) if param_names else ""

    func_code = f"""
async def {tool_name}({params_str}) -> str:
    '''{docstring}'''
    params = {{{collect_str}}}
    return await _do_call(params)
"""

    # Execute to create the function
    local_ns = {"_do_call": _do_call}
    exec(func_code, local_ns)
    func = local_ns[tool_name]

    # Wrap with @function_tool
    tool = function_tool(func, name_override=tool_name)

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
