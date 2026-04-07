"""
Aleph Framework — FastAPI Webhooks
==========================================
The entry point. Receives Z-API webhooks and drives the full pipeline:
  webhook → filter → anti-spam → buffer → [wait] → consume → lock → run agent → send

Endpoints:
  POST /webhook/zapi   — Main message handler
  POST /webhook/humano — Human-in-the-loop reply (escalation response)
  GET  /health         — Health check

Usage:
  python -m core.api.webhooks --client example
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
from core.engine.pipeline import process_message
from core.messaging.zapi_send import ZAPISender
from core.session.memory import EpisodicMemory

logger = logging.getLogger("aleph.api")

# ---------------------------------------------------------------------------
# Global state (initialized on startup)
# ---------------------------------------------------------------------------

_registry: AgentRegistry | None = None
_redis: RedisSession | None = None
_sender: ZAPISender | None = None
_habits_db = None  # HabitsDatabase | None — initialized only if habits.enabled
_buffer_timers: dict[str, asyncio.Task] = {}
_knowledge_db = None
_flow_engine = None  # FlowEngine | None — initialized only if flows.enabled
_memory = None  # EpisodicMemory | None — initialized on startup


# ---------------------------------------------------------------------------
# App lifecycle
# ---------------------------------------------------------------------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Boot the framework on startup, cleanup on shutdown."""
    global _registry, _redis, _sender, _habits_db, _knowledge_db, _flow_engine, _memory

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

    # Init Habits database (only if enabled)
    if _registry.config.habits.enabled:
        try:
            from core.habits.database import HabitsDatabase

            _habits_db = HabitsDatabase(_registry.config.habits)
            await _habits_db.connect()
            await _habits_db.bootstrap()
            logger.info("Habits database connected and bootstrapped")
        except Exception as e:
            logger.error(
                "Habits database initialization failed: %s. "
                "Habits will be disabled for this session.",
                str(e)[:200],
            )
            _habits_db = None
            
    # Init Knowledge database (only if enabled)
    if _registry.config.knowledge.enabled:
        try:
            from core.knowledge.database import KnowledgeDatabase

            _knowledge_db = KnowledgeDatabase(_registry.config.knowledge)
            await _knowledge_db.connect()
            await _knowledge_db.bootstrap()
            logger.info("Knowledge database connected and bootstrapped")
        except Exception as e:
            logger.error(
                "Knowledge database initialization failed: %s. "
                "Knowledge will be disabled for this session.",
                str(e)[:200],
            )
            _knowledge_db = None

    # Init FlowEngine (only if flows.enabled)
    if _registry.config.flows.enabled:
        from core.flows import FlowEngine
        _flow_engine = FlowEngine(_registry.config.flows)
        logger.info("FlowEngine initialized with %d flow(s)", len(_registry.config.flows.flows))

    # Boot EpisodicMemory (always on — falls back to in-memory if Redis unavailable)
    redis_client = _redis.client if _redis else None
    _memory = EpisodicMemory(_registry.config, redis_client=redis_client)
    logger.info("EpisodicMemory initialized (redis=%s)", redis_client is not None)

    logger.info(
        "🚀 %s online — port %d — model %s — habits %s — flows %s",
        _registry.agent_name,
        _registry.config.api.port,
        _registry.config.agent.model,
        "ON" if _habits_db else "OFF",
        "ON" if _flow_engine else "OFF",
    )

    yield

    # Cleanup
    if _knowledge_db:
        await _knowledge_db.close()
    if _habits_db:
        await _habits_db.close()
    if _sender:
        await _sender.close()
    if _redis:
        await _redis.close()
    logger.info("Shutdown complete")


app = FastAPI(title="Aleph Framework", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Health check
# ---------------------------------------------------------------------------

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "agent": _registry.agent_name if _registry else None,
        "client_id": _registry.client_id if _registry else None,
        "habits": _habits_db is not None,
        "timestamp": time.time(),
    }


# ---------------------------------------------------------------------------
# Main webhook — Z-API
# ---------------------------------------------------------------------------

@app.post("/webhook/zapi")
async def webhook_zapi(request: Request):
    """Main Z-API webhook handler."""
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
    if is_human_reply(message, _registry.config.human.responsible_phones):
        reference_id = message.get("reference_message_id", "")
        if reference_id:
            logger.info(
                "Human reply detected from %s (ref: %s)", phone, reference_id,
            )
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

    # --- Check takeover active ---
    if await _redis.is_takeover_active(phone):
        logger.debug("Takeover active, ignoring message from %s", phone)
        return JSONResponse({"status": "takeover_active"})

    # --- Anti-spam ---
    if message_id and await _redis.is_duplicate(message_id):
        return JSONResponse({"status": "duplicate"})

    # --- Buffer chunked messages ---
    await _redis.buffer_message(phone, text)

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
        await asyncio.sleep(_registry.config.session.buffer_timeout)

        _buffer_timers.pop(phone, None)

        if await _redis.is_takeover_active(phone):
            await _redis.consume_buffer(phone)
            return

        consolidated = await _redis.consume_buffer(phone)
        if not consolidated:
            return

        if not await _redis.acquire_lock(phone):
            logger.warning("Lock busy for %s, skipping", phone)
            return

        try:
            result = await process_message(
                registry=_registry,
                user_message=consolidated,
                phone=phone,
                redis_session=_redis,
                sender=_sender,
                habits_db=_habits_db,
                knowledge_db=_knowledge_db,
                flow_engine=_flow_engine,
                episodic_memory=_memory,     # ← NEW
            )

            logger.info(
                "Pipeline responded to %s: %d chars in %.1fs "
                "(skipped_llm=%s, escalated=%s, habit=%s)",
                phone, len(result.response), result.elapsed_seconds,
                result.skipped_llm, result.escalated, result.habit_used,
            )

            await _sender.send_response(phone, result.response)

        finally:
            await _redis.release_lock(phone)

    except asyncio.CancelledError:
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
    """Handle a human's reply to an escalation notification."""
    try:
        from core.human.escalation import handle_human_response

        success = await handle_human_response(
            redis_session=_redis,
            sender=_sender,
            registry=_registry,
            responsible_phone=responsible_phone,
            human_instruction=human_instruction,
            reference_message_id=reference_message_id,
            habits_db=_habits_db,
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
# Human webhook (kept for external integrations)
# ---------------------------------------------------------------------------

@app.post("/webhook/humano")
async def webhook_humano(request: Request):
    """Human-in-the-loop reply webhook for external integrations."""
    try:
        payload = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid json"}, status_code=400)

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
        await _handle_escalation_reply(
            responsible_phone, human_instruction, reference_id,
        )
    else:
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

    parser = argparse.ArgumentParser(description="Aleph Framework — Server")
    parser.add_argument("--client", type=str, help="Client ID (overrides CLIENT_ID env var)")
    parser.add_argument("--port", type=int, help="Port override")
    parser.add_argument("--log-level", type=str, default="INFO")
    args = parser.parse_args()

    if args.client:
        os.environ["CLIENT_ID"] = args.client

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper()),
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

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