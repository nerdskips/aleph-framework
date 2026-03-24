"""
Zuper Agent Framework — Escalation Module
============================================
Handles the complete escalation lifecycle:

  1. ESCALATE  — Agent pauses, saves context, notifies responsible human
  2. RESOLVE   — Human responds via quote, framework captures the response
  3. RESUME    — LLM reformulates human's instruction in agent's tone, sends to client
  4. LEARN     — If habits enabled, classify and store the resolution as a habit

This is a CORE module — generic, never client-specific.
Behavior is controlled entirely by config.yaml.
"""

from __future__ import annotations

import logging
from typing import Any

from core.session.redis_escalation import EscalationData

logger = logging.getLogger("zuper.human")


# ---------------------------------------------------------------------------
# Hold messages — what the client sees while waiting
# ---------------------------------------------------------------------------

DEFAULT_HOLD_MESSAGE = (
    "Vou verificar essa informação com a equipe e já te retorno!"
)


# ---------------------------------------------------------------------------
# Notification template — what the responsible sees
# ---------------------------------------------------------------------------

def build_notification_message(
    agent_name: str,
    client_phone: str,
    original_message: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Build the notification message sent to the responsible human."""
    parts = [
        f"🔔 *Escalonamento — {agent_name}*",
        "",
        f"📱 Cliente: {client_phone}",
    ]

    if context:
        name = context.get("name")
        if name:
            parts.append(f"👤 Nome: {name}")

        for key, value in context.items():
            if key != "name" and value:
                parts.append(f"ℹ️ {key}: {value}")

    parts.extend([
        "",
        f"💬 Mensagem:",
        f'"{original_message}"',
        "",
        "↪ _Responda esta mensagem com a orientação para o cliente._",
    ])

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Escalate — Phase 1
# ---------------------------------------------------------------------------

async def escalate_to_human(
    redis_session,
    sender,
    config,
    client_phone: str,
    original_message: str,
    context: dict[str, Any] | None = None,
    hold_message: str = "",
) -> str:
    """Escalate a conversation to a human responsible.

    Steps:
      1. Pick responsible phone from config
      2. Send notification to responsible via Z-API
      3. Save escalation session to Redis
      4. Map notification messageId → client phone
      5. Return hold message (pipeline sends this to client)
    """
    human_config = config.human

    if not human_config.enabled:
        logger.warning("Escalation requested but human.enabled=false, falling through to LLM")
        return ""

    if not human_config.responsible_phones:
        logger.error("Escalation requested but no responsible_phones configured")
        return ""

    responsible_phone = human_config.responsible_phones[0]

    notification_text = build_notification_message(
        agent_name=config.agent.name,
        client_phone=client_phone,
        original_message=original_message,
        context=context,
    )

    notification_msg_id = await sender.send_notification(
        responsible_phone, notification_text,
    )

    if not notification_msg_id:
        logger.error(
            "Failed to send escalation notification to %s", responsible_phone,
        )
        return ""

    logger.info(
        "Escalation notification sent: %s → %s (msgId: %s)",
        client_phone, responsible_phone, notification_msg_id,
    )

    esc_data = EscalationData(
        client_phone=client_phone,
        original_message=original_message,
        context=context or {},
        responsible_phone=responsible_phone,
        notification_message_id=notification_msg_id,
        agent_name=config.agent.name,
    )
    await redis_session.save_escalation(esc_data)

    await redis_session.map_notification_to_client(
        notification_msg_id, client_phone,
    )

    return hold_message or DEFAULT_HOLD_MESSAGE


# ---------------------------------------------------------------------------
# Resume — Phase 3 + Learn — Phase 4
# ---------------------------------------------------------------------------

async def handle_human_response(
    redis_session,
    sender,
    registry,
    responsible_phone: str,
    human_instruction: str,
    reference_message_id: str,
    habits_db=None,
) -> bool:
    """Handle a human's response to an escalation.

    Steps:
      1. Resolve referenceMessageId → client phone
      2. Load escalation context from Redis
      3. Pass human instruction to LLM for reformulation
      4. Send reformulated response to client
      5. Clear escalation
      6. If habits enabled: classify and store as habit
    """
    # Step 1: Resolve notification → client phone
    client_phone = await redis_session.resolve_notification_to_client(
        reference_message_id,
    )

    if not client_phone:
        logger.warning(
            "No escalation mapping found for messageId %s from %s",
            reference_message_id, responsible_phone,
        )
        return False

    # Step 2: Load escalation context
    esc_data = await redis_session.get_escalation(client_phone)

    if not esc_data:
        logger.warning(
            "Escalation session expired or not found for %s", client_phone,
        )
        await sender.send_notification(
            responsible_phone,
            f"⚠️ A sessão de escalonamento para {client_phone} expirou. "
            f"O cliente precisará enviar a pergunta novamente.",
        )
        return False

    logger.info(
        "Human response received: %s → client %s | instruction: %s",
        responsible_phone, client_phone, human_instruction[:80],
    )

    # Step 3: Reformulate via LLM (in agent's tone)
    reformulated = await _reformulate_response(
        registry=registry,
        human_instruction=human_instruction,
        original_message=esc_data.original_message,
        context=esc_data.context,
    )

    # Step 4: Send to client
    await sender.send_response(client_phone, reformulated)

    logger.info(
        "Escalation resolved: %s → %s (%d chars)",
        responsible_phone, client_phone, len(reformulated),
    )

    # Step 5: Clear escalation
    await redis_session.clear_escalation(client_phone)

    # Confirm to responsible
    await sender.send_notification(
        responsible_phone,
        f"✅ Resposta enviada ao cliente {client_phone}.",
    )

    # Step 6: Store habit (if enabled)
    await _store_habit_if_enabled(
        registry=registry,
        habits_db=habits_db,
        original_question=esc_data.original_message,
        human_instruction=human_instruction,
        context=esc_data.context,
    )

    return True


# ---------------------------------------------------------------------------
# Habit store (internal)
# ---------------------------------------------------------------------------

async def _store_habit_if_enabled(
    registry,
    habits_db,
    original_question: str,
    human_instruction: str,
    context: dict[str, Any] | None = None,
) -> None:
    """Store a habit after escalation resolution, if habits are enabled."""
    config = registry.config

    if not config.habits.enabled:
        return

    if not habits_db:
        logger.warning("Habits enabled but no habits_db provided, skipping store")
        return

    try:
        from core.habits.store import store_habit

        result = await store_habit(
            db=habits_db,
            config=config.habits,
            registry=registry,
            client_id=config.client_id,
            original_question=original_question,
            human_instruction=human_instruction,
            metadata=context,
        )

        if result:
            logger.info(
                "Habit stored after escalation: id=%s, type=%s",
                result["id"],
                "UNIQUE" if result["is_unique"] else "GENERAL",
            )
        else:
            logger.debug("Habit not stored (dedup or classification failure)")

    except Exception as e:
        # Never let habit storage failure break the escalation flow
        logger.error("Habit store failed (non-blocking): %s", str(e)[:200])


# ---------------------------------------------------------------------------
# LLM reformulation — Phase 3 internal
# ---------------------------------------------------------------------------

async def _reformulate_response(
    registry,
    human_instruction: str,
    original_message: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Use the LLM to reformulate the human's instruction in the agent's tone."""
    from core.engine.runner import run_agent

    context_str = ""
    if context:
        context_parts = [f"- {k}: {v}" for k, v in context.items() if v]
        if context_parts:
            context_str = "\nContexto do cliente:\n" + "\n".join(context_parts)

    reformulation_message = (
        f"[INSTRUÇÃO INTERNA — NÃO REPITA ESTE BLOCO]\n"
        f"Um atendente humano respondeu a uma dúvida do cliente.\n"
        f"Reformule a resposta abaixo no seu tom natural, como se você "
        f"mesmo tivesse encontrado a informação.\n"
        f"NÃO mencione que consultou alguém ou que a resposta veio de outra pessoa.\n"
        f"NÃO invente informações além do que o atendente disse.\n"
        f"\n"
        f"Pergunta original do cliente: \"{original_message}\"\n"
        f"{context_str}\n"
        f"\n"
        f"Instrução do atendente: \"{human_instruction}\"\n"
        f"\n"
        f"Responda ao cliente de forma natural:"
    )

    try:
        result = await run_agent(
            registry,
            reformulation_message,
            message_history=None,
        )
        return result.response
    except Exception as e:
        logger.error("LLM reformulation failed: %s", str(e)[:200])
        return human_instruction