"""
Zuper Agent Framework — FastAPI Webhooks
==========================================
The entry point. Receives Z-API webhooks and drives the full pipeline:
  webhook → filter → anti-spam → buffer → [wait] → consume → lock → run agent → send

Endpoints:
  POST /webhook/zapi   — Main message handler
  POST /webhook/humano — Human-in-the-loop reply (escalation response)
  GET  /health         — Health check

Usage:
  python -m core.api.webhooks --client example
  # Starts FastAPI on configured port, ready to receive Z-API webhooks
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from contextlib import asynccontextmanager

from dotenv import load_dotenv
load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from core.registry.registry import AgentRegistry
from core.session.redis import RedisSession
from core.messaging.zapi_filter import (
    extract_message,
    should_filter,
    is_human_takeover_message,
    is_human_reply,
)
from core.messaging.zapi_send import ZAPISender
from core.engine.pipeline import process_message

logger = logging.getLogger("zuper.api")

# ---------------------------------------------------------------------------
# Global state (initialized on startup)
# ---------------------------------------------------------------------------

_registry: AgentRegistry | None = None
_redis: RedisSession | None = None
_sender: ZAPISender | None = None
_buffer_timers: dict[str, asyncio.Task] = {}


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot the framework on startup, cleanup on shutdown."""
    global _registry, _redis, _sender

    client_id = os.environ.get("CLIENT_ID")
    if not client_id:
        raise ValueError("CLIENT_ID environment variable not set")

    # Boot registry
    _registry = AgentRegistry.from_config(client_id=client_id)
    logger.info("Registry loaded: %s", _registry.agent_name)

    # Connect Redis
    _redis = RedisSession(_registry.config)
    await _redis.connect()

    # Init Z-API sender
    _sender = ZAPISender(_registry.config)

    logger.info(
        "🚀 %s online — port %d — model %s",
        _registry.agent_name,
        _registry.config.api.port,
        _registry.config.agent.model,
    )

    yield

    # Cleanup
    if _sender:
        await _sender.close()
    if _redis:
        await _redis.close()
    logger.info("Shutdown complete")


app = FastAPI(title="Zuper Agent Framework", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": _registry.agent_name if _registry else None,
        "client_id": _registry.client_id if _registry else None,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Main webhook — Z-API
# ---------------------------------------------------------------------------

@app.post("/webhook/zapi")
async def webhook_zapi(request: Request):
    """Main Z-API webhook handler.

    Flow:
      1. Parse payload
      2. Filter (groups, newsletters, reactions, etc)
      3. Check for human reply (escalation response via quote)
      4. Handle takeover (human typing on agent's WhatsApp)
      5. Anti-spam (messageId dedup)
      6. Buffer (chunked messages consolidation)
      7. After buffer timeout: consume → lock → run agent → send
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # Extract message
    message = extract_message(payload)
    if not message:
        return JSONResponse({"status": "ignored"})

    phone = message["phone"]
    text = message["text"]
    message_id = message["message_id"]

    # --- Filter ---
    filter_reason = should_filter(message, _registry.config)
    if filter_reason:
        logger.debug("Filtered [%s]: %s", filter_reason, phone)
        return JSONResponse({"status": "filtered", "reason": filter_reason})

    # --- Human reply detection (escalation response) ---
    # Check BEFORE takeover — a quote from responsible is an escalation reply,
    # not a takeover. This handles the case where the responsible is also
    # listed as someone who types on the agent's WhatsApp.
    if is_human_reply(message, _registry.config.human.responsible_phones):
        reference_id = message.get("reference_message_id", "")
        if reference_id:
            logger.info(
                "Human reply detected from %s (ref: %s)", phone, reference_id,
            )
            # Handle async to not block webhook response
            asyncio.create_task(
                _handle_escalation_reply(phone, text, reference_id)
            )
            return JSONResponse({"status": "escalation_reply_received"})

    # --- Takeover detection ---
    if is_human_takeover_message(message):
        raw_text = text.strip().upper()
        release_keyword = _registry.config.human.release_keyword

        if raw_text == release_keyword:
            await _redis.release_takeover(phone)
            logger.info("Takeover released via %s for %s", release_keyword, phone)
        else:
            await _redis.activate_takeover(phone)
            if _registry.config.human.takeover_renew_on_message:
                await _redis.renew_takeover(phone)
        return JSONResponse({"status": "takeover_handled"})

    # --- Check takeover active (consume buffer silently) ---
    if await _redis.is_takeover_active(phone):
        logger.debug("Takeover active, ignoring message from %s", phone)
        return JSONResponse({"status": "takeover_active"})

    # --- Anti-spam ---
    if message_id and await _redis.is_duplicate(message_id):
        return JSONResponse({"status": "duplicate"})

    # --- Buffer chunked messages ---
    await _redis.buffer_message(phone, text)

    # Cancel previous timer for this phone, start new one
    if phone in _buffer_timers:
        _buffer_timers[phone].cancel()

    _buffer_timers[phone] = asyncio.create_task(
        _process_after_buffer(phone)
    )

    return JSONResponse({"status": "buffered"})


# ---------------------------------------------------------------------------
# Buffer → Process → Respond
# ---------------------------------------------------------------------------

async def _process_after_buffer(phone: str):
    """Wait for buffer timeout, then process the consolidated message."""
    try:
        # Wait for more chunks
        await asyncio.sleep(_registry.config.session.buffer_timeout)

        # Remove timer reference
        _buffer_timers.pop(phone, None)

        # Check takeover again (might have been activated during buffer wait)
        if await _redis.is_takeover_active(phone):
            await _redis.consume_buffer(phone)  # consume and discard
            return

        # Consume buffer
        consolidated = await _redis.consume_buffer(phone)
        if not consolidated:
            return

        # Acquire processing lock
        if not await _redis.acquire_lock(phone):
            logger.warning("Lock busy for %s, skipping", phone)
            return

        try:
            # Run full pipeline (guardrails + agent + output check)
            # Pass phone, redis, and sender for escalation support
            result = await process_message(
                registry=_registry,
                user_message=consolidated,
                phone=phone,
                redis_session=_redis,
                sender=_sender,
            )

            logger.info(
                "Pipeline responded to %s: %d chars in %.1fs "
                "(skipped_llm=%s, escalated=%s)",
                phone, len(result.response), result.elapsed_seconds,
                result.skipped_llm, result.escalated,
            )

            # Send response
            await _sender.send_response(phone, result.response)

        finally:
            await _redis.release_lock(phone)

    except asyncio.CancelledError:
        # Timer was cancelled because a new message arrived
        pass
    except Exception as e:
        logger.error("Error processing message for %s: %s", phone, str(e), exc_info=True)


# ---------------------------------------------------------------------------
# Escalation reply handler
# ---------------------------------------------------------------------------

async def _handle_escalation_reply(
    responsible_phone: str,
    human_instruction: str,
    reference_message_id: str,
):
    """Handle a human's reply to an escalation notification.

    Called when a responsible person responds with a quote to the
    escalation notification message.
    """
    try:
        from core.human.escalation import handle_human_response

        success = await handle_human_response(
            redis_session=_redis,
            sender=_sender,
            registry=_registry,
            responsible_phone=responsible_phone,
            human_instruction=human_instruction,
            reference_message_id=reference_message_id,
        )

        if success:
            logger.info(
                "Escalation resolved by %s (ref: %s)",
                responsible_phone, reference_message_id,
            )
        else:
            logger.warning(
                "Escalation reply from %s could not be resolved (ref: %s)",
                responsible_phone, reference_message_id,
            )

    except Exception as e:
        logger.error(
            "Error handling escalation reply from %s: %s",
            responsible_phone, str(e), exc_info=True,
        )


# ---------------------------------------------------------------------------
# Human webhook (legacy endpoint — kept for compatibility)
# ---------------------------------------------------------------------------

@app.post("/webhook/humano")
async def webhook_humano(request: Request):
    """Human-in-the-loop reply webhook.

    NOTE: In the current architecture, escalation replies come through
    the main /webhook/zapi endpoint (same Z-API instance) and are
    detected by is_human_reply(). This endpoint is kept for:
      - Future use with separate notification channels (N8N, panels)
      - Direct API integration (external systems calling back)
    """
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

    # Accept direct API calls with structured payload
    client_phone = payload.get("client_phone", "")
    human_instruction = payload.get("instruction", "")
    reference_id = payload.get("reference_message_id", "")
    responsible_phone = payload.get("responsible_phone", "")

    if not all([client_phone, human_instruction]):
        return JSONResponse(
            {"error": "missing client_phone or instruction"},
            status_code=400,
        )

    if reference_id:
        # Standard flow: resolve via notification mapping
        await _handle_escalation_reply(
            responsible_phone, human_instruction, reference_id,
        )
    else:
        # Direct resolution: client_phone provided explicitly
        from core.human.escalation import handle_human_response
        # For direct calls, we need to find the escalation by phone
        esc_data = await _redis.get_escalation(client_phone)
        if esc_data:
            await _handle_escalation_reply(
                responsible_phone or "api",
                human_instruction,
                esc_data.notification_message_id,
            )
        else:
            return JSONResponse(
                {"error": "no active escalation for this phone"},
                status_code=404,
            )

    return JSONResponse({"status": "processed"})


# ---------------------------------------------------------------------------
# CLI entrypoint
# ---------------------------------------------------------------------------

def main():
    """CLI: python -m core.api.webhooks --client example"""
    import argparse
    import uvicorn

    parser = argparse.ArgumentParser(description="Zuper Agent Framework — Server")
    parser.add_argument("--client", type=str, help="Client ID (overrides CLIENT_ID env var)")
    parser.add_argument("--port", type=int, help="Port override")
    parser.add_argument("--log-level", type=str, default="INFO")
    args = parser.parse_args()

    # Set CLIENT_ID for lifespan to pick up
    if args.client:
        os.environ["CLIENT_ID"] = args.client

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    # Load config just to get the port
    client_id = os.environ.get("CLIENT_ID")
    if not client_id:
        print("❌ Set CLIENT_ID env var or pass --client")
        return

    from core.registry.loader import load_config
    config = load_config(client_id=client_id)
    port = args.port or config.api.port

    print(f"🚀 Starting {config.agent.name} on port {port}...")
    uvicorn.run(
        "core.api.webhooks:app",
        host=config.api.host,
        port=port,
        log_level=args.log_level.lower(),
    )


if __name__ == "__main__":
    main()