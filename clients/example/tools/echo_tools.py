"""
Echo Bot — Domain Tools
========================
Simplest possible tool to validate the framework's
tool loading and execution pipeline.
"""

from agents import function_tool


@function_tool
def echo_message(message: str) -> str:
    """
    Echo the user's message back with metadata.
    Use this when the user asks to test the echo functionality.

    Args:
        message: The message to echo back
    """
    return (
        f"🔁 Echo: {message}\n"
        f"📐 Length: {len(message)} chars\n"
        f"✅ Framework pipeline: OK"
    )
