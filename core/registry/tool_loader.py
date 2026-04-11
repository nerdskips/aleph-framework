"""
Aleph Framework — Dynamic Tool Loader
=============================================
Imports tool modules from a client's tools/ directory and extracts
functions decorated with @function_tool (from OpenAI Agents SDK).

How it works:
  1. Reads ToolRef list from config (module name + optional function names)
  2. Adds the client's tools/ dir to sys.path temporarily
  3. Imports each module
  4. Extracts decorated functions (or explicitly named ones)
  5. Returns a flat list of tool objects ready for the SDK Agent

Graceful degradation:
  If the SDK is not installed, tool_loader still validates that modules
  exist and are importable — useful for boot checks without full deps.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from typing import Any

from core.registry.schema import FrameworkConfig, ToolRef


# ---------------------------------------------------------------------------
# Tool detection
# ---------------------------------------------------------------------------

def _is_function_tool(obj: Any) -> bool:
    """Check if an object is a @function_tool decorated function.

    The SDK wraps functions in a FunctionTool object.
    We check by class name to avoid hard dependency on the SDK at import time.
    """
    type_name = type(obj).__name__
    # SDK 0.12+ wraps @function_tool as FunctionTool
    if type_name == "FunctionTool":
        return True
    # Also check for the attribute the decorator sets
    if hasattr(obj, "__tool_metadata__"):
        return True
    return False


def _extract_tools_from_module(module: Any, function_names: list[str]) -> list[Any]:
    """Extract tool functions from an imported module.

    Args:
        module: The imported Python module
        function_names: Specific function names to extract.
                       Empty list = extract all @function_tool decorated.

    Returns:
        List of tool objects (FunctionTool instances or callables)
    """
    tools = []

    if function_names:
        # Explicit list: grab exactly these functions
        for name in function_names:
            obj = getattr(module, name, None)
            if obj is None:
                raise AttributeError(
                    f"Function '{name}' not found in module '{module.__name__}'. "
                    f"Available: {[n for n in dir(module) if not n.startswith('_')]}"
                )
            tools.append(obj)
    else:
        # Auto-discover: find all @function_tool decorated objects
        for attr_name in dir(module):
            if attr_name.startswith("_"):
                continue
            obj = getattr(module, attr_name)
            if _is_function_tool(obj):
                tools.append(obj)

        # If no decorated tools found, look for callables with 'tool' in the name
        # (fallback for when SDK isn't installed but we want to validate structure)
        if not tools:
            for attr_name in dir(module):
                if attr_name.startswith("_"):
                    continue
                obj = getattr(module, attr_name)
                if callable(obj) and "tool" not in type(obj).__name__.lower():
                    # Skip classes, only grab plain functions
                    if hasattr(obj, "__call__") and not isinstance(obj, type):
                        # Only include if it looks like a tool (has docstring, takes args)
                        if hasattr(obj, "__doc__") and obj.__doc__:
                            tools.append(obj)

    return tools


# ---------------------------------------------------------------------------
# Module importing
# ---------------------------------------------------------------------------

def _import_module_from_path(module_name: str, tools_dir: Path) -> Any:
    """Import a Python module from a specific directory.

    Uses importlib to load from the client's tools/ folder without
    permanently polluting sys.path.

    Args:
        module_name: Module name (e.g. 'echo_tools')
        tools_dir: Path to the tools/ directory

    Returns:
        The imported module object

    Raises:
        ImportError: if module can't be found or imported
    """
    module_file = tools_dir / f"{module_name}.py"
    if not module_file.is_file():
        raise ImportError(
            f"Tool module not found: {module_file}\n"
            f"Available modules: {_list_tool_modules(tools_dir)}"
        )

    spec = importlib.util.spec_from_file_location(
        f"client_tools.{module_name}",
        module_file,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for: {module_file}")

    module = importlib.util.module_from_spec(spec)

    # Temporarily add tools dir to path so relative imports within tools work
    tools_dir_str = str(tools_dir)
    path_added = False
    if tools_dir_str not in sys.path:
        sys.path.insert(0, tools_dir_str)
        path_added = True

    try:
        spec.loader.exec_module(module)
    except Exception as e:
        raise ImportError(
            f"Error importing tool module '{module_name}' from {module_file}: {e}"
        ) from e
    finally:
        if path_added:
            sys.path.remove(tools_dir_str)

    return module


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def load_tools(config: FrameworkConfig) -> list[Any]:
    """Load all tools defined in the client's config.

    Supports two modes:
      - type='code': imports Python module from client's tools/ folder
      - type='webhook': generates @function_tool from YAML webhook definition

    Args:
        config: Validated FrameworkConfig

    Returns:
        Flat list of tool objects ready to pass to Agent(tools=[...])

    Raises:
        ImportError: if a code tool module can't be found or imported
        AttributeError: if an explicitly named function doesn't exist
    """
    from core.registry.schema import ToolType
    from core.tools.webhook import generate_webhook_tools

    all_tools = []

    # --- Webhook tools (generated from YAML, zero code) ---
    webhook_refs = [t for t in config.tools if t.type == ToolType.WEBHOOK]
    if webhook_refs:
        webhook_tools = generate_webhook_tools(webhook_refs)
        all_tools.extend(webhook_tools)

    # --- Code tools (Python modules in client's tools/ folder) ---
    code_refs = [t for t in config.tools if t.type == ToolType.CODE]
    if code_refs:
        tools_dir = config.client_dir / "tools"

        if not tools_dir.is_dir() and code_refs:
            raise FileNotFoundError(
                f"Tools directory not found: {tools_dir}\n"
                f"Config references {len(code_refs)} code tool(s) but directory doesn't exist."
            )

        for tool_ref in code_refs:
            if not tool_ref.module:
                import warnings
                warnings.warn(
                    f"Code tool '{tool_ref.name}' has no module specified, skipping.",
                    stacklevel=2,
                )
                continue

            module = _import_module_from_path(tool_ref.module, tools_dir)
            extracted = _extract_tools_from_module(module, tool_ref.functions)

            if not extracted:
                import warnings
                warnings.warn(
                    f"No tools found in module '{tool_ref.module}'. "
                    f"Ensure functions are decorated with @function_tool.",
                    stacklevel=2,
                )

            all_tools.extend(extracted)

    return all_tools


def build_tools_from_config(tools: list, client_dir: Any) -> list[Any]:
    """Build tools from a list of ToolRef objects and a client directory.

    Variant of load_tools() that accepts tools+dir directly instead of a
    full FrameworkConfig — used by sub-agent construction in runner.py.

    Args:
        tools:      List of ToolRef objects
        client_dir: Path to the client directory (for code tool imports)

    Returns:
        Flat list of tool objects ready to pass to Agent(tools=[...])
    """
    from core.registry.schema import ToolType
    from core.tools.webhook import generate_webhook_tools

    all_tools = []

    webhook_refs = [t for t in tools if t.type == ToolType.WEBHOOK]
    if webhook_refs:
        all_tools.extend(generate_webhook_tools(webhook_refs))

    code_refs = [t for t in tools if t.type == ToolType.CODE]
    if code_refs:
        from pathlib import Path as _Path
        tools_dir = _Path(client_dir) / "tools"
        for tool_ref in code_refs:
            if not tool_ref.module:
                continue
            module = _import_module_from_path(tool_ref.module, tools_dir)
            all_tools.extend(_extract_tools_from_module(module, tool_ref.functions))

    return all_tools


def validate_tools(config: FrameworkConfig) -> dict[str, list[str]]:
    """Validate tool modules exist without importing them.
    Useful for quick config validation without SDK dependency.

    Returns:
        Dict mapping module name to list of .py files found.
    """
    tools_dir = config.client_dir / "tools"
    result = {}

    for tool_ref in config.tools:
        module_file = tools_dir / f"{tool_ref.module}.py"
        result[tool_ref.module] = {
            "exists": module_file.is_file(),
            "path": str(module_file),
            "functions_requested": tool_ref.functions or ["(auto-discover)"],
        }

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _list_tool_modules(tools_dir: Path) -> list[str]:
    """List available .py files in a tools directory."""
    if not tools_dir.is_dir():
        return []
    return sorted(
        f.stem for f in tools_dir.glob("*.py")
        if f.stem != "__init__"
    )
