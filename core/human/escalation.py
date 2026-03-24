"""
Zuper Agent Framework — Escalation Module
============================================
Handles the complete escalation lifecycle:

  1. ESCALATE  — Agent pauses, saves context, notifies responsible human
  2. RESOLVE   — Human responds via quote, framework captures the response
  3. RESUME    — LLM reformulates human's instruction in agent's tone, sends to client

This is a CORE module — generic, never client-specific.
Behavior is controlled entirely by config.yaml:
  - human.enabled
  - human.responsible_phones
  - human.escalation_session_ttl
  - human.notify_via

Flow:
  Pipeline detects ESCALATE action (guardrail or tool)
    → escalate_to_human()
      → saves session to Redis (esc:{phone})
      → picks responsible from config
      → sends notification via Z-API
      → maps notification messageId → client phone
      → returns hold message to client

  Human responds with quote on notification
    → handle_human_response() (called from /webhook/humano)
      → extracts referenceMessageId from quote
      → resolves to client phone via Redis mapping
      → loads escalation context
      → passes human instruction + context to LLM
      → LLM reformulates in agent's tone
      → sends to client via Z-API
      → clears escalation
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
    """Build the notification message sent to the responsible human.

    The message includes enough context for the human to respond
    without needing to look anything up.

    Args:
        agent_name: Name of the agent (from config)
        client_phone: Client's phone number
        original_message: The message that triggered escalation
        context: Optional client context (name, preferences, etc)
    """
    parts = [
        f"🔔 *Escalonamento — {agent_name}*",
        "",
        f"📱 Cliente: {client_phone}",
    ]

    # Add context if available
    if context:
        name = context.get("name")
        if name:
            parts.append(f"👤 Nome: {name}")

        # Add other relevant context (neighborhood, preferences, etc)
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

    This is called by the pipeline when a guardrail triggers ESCALATE.

    Steps:
      1. Pick responsible phone from config
      2. Send notification to responsible via Z-API
      3. Save escalation session to Redis
      4. Map notification messageId → client phone
      5. Return hold message (pipeline sends this to client)

    Args:
        redis_session: RedisSession instance
        sender: ZAPISender instance
        config: FrameworkConfig
        client_phone: The client's phone number
        original_message: The message that triggered escalation
        context: Optional client context from Redis
        hold_message: Custom hold message (falls back to default)

    Returns:
        Hold message string to send to the client
    """
    human_config = config.human

    if not human_config.enabled:
        logger.warning("Escalation requested but human.enabled=false, falling through to LLM")
        return ""

    if not human_config.responsible_phones:
        logger.error("Escalation requested but no responsible_phones configured")
        return ""

    # Pick first responsible (future: round-robin, availability check)
    responsible_phone = human_config.responsible_phones[0]

    # Build notification
    notification_text = build_notification_message(
        agent_name=config.agent.name,
        client_phone=client_phone,
        original_message=original_message,
        context=context,
    )

    # Send notification to responsible
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

    # Save escalation session
    esc_data = EscalationData(
        client_phone=client_phone,
        original_message=original_message,
        context=context or {},
        responsible_phone=responsible_phone,
        notification_message_id=notification_msg_id,
        agent_name=config.agent.name,
    )
    await redis_session.save_escalation(esc_data)

    # Map notification messageId → client phone (for quote lookup)
    await redis_session.map_notification_to_client(
        notification_msg_id, client_phone,
    )

    return hold_message or DEFAULT_HOLD_MESSAGE


# ---------------------------------------------------------------------------
# Resume — Phase 3
# ---------------------------------------------------------------------------

async def handle_human_response(
    redis_session,
    sender,
    registry,
    responsible_phone: str,
    human_instruction: str,
    reference_message_id: str,
) -> bool:
    """Handle a human's response to an escalation.

    Called from /webhook/humano when a responsible replies with a quote.

    Steps:
      1. Resolve referenceMessageId → client phone
      2. Load escalation context from Redis
      3. Pass human instruction to LLM for reformulation
      4. Send reformulated response to client
      5. Clear escalation

    Args:
        redis_session: RedisSession instance
        sender: ZAPISender instance
        registry: AgentRegistry (for running LLM)
        responsible_phone: Phone of the human who responded
        human_instruction: The human's response text
        reference_message_id: messageId being quoted (our notification)

    Returns:
        True if handled successfully, False if escalation not found
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
        # Notify responsible that the session expired
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

    return True


# ---------------------------------------------------------------------------
# LLM reformulation — Phase 3 internal
# ---------------------------------------------------------------------------

async def _reformulate_response(
    registry,
    human_instruction: str,
    original_message: str,
    context: dict[str, Any] | None = None,
) -> str:
    """Use the LLM to reformulate the human's instruction in the agent's tone.

    The human writes a raw instruction like:
      "Diga que o prazo é 5 dias úteis e que pode acompanhar pelo app"

    The LLM turns it into a natural response in the agent's voice:
      "O prazo para resolução é de 5 dias úteis! Você pode acompanhar
       o andamento pelo nosso app a qualquer momento 😊"

    Args:
        registry: AgentRegistry (has agent + runner)
        human_instruction: Raw instruction from the human
        original_message: What the client originally asked
        context: Optional client context

    Returns:
        Reformulated response string
    """
    from core.engine.runner import run_agent

    # Build reformulation prompt
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
        # Fallback: send the human instruction as-is
        return human_instruction