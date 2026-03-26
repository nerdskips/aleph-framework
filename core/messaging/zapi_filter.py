"""
Aleph Framework — Z-API Webhook Filter
===============================================
Filters incoming Z-API webhook payloads.
Discards non-message events before they reach the pipeline.

All 11+ filters from Laura production, configured via config.yaml.
"""

from __future__ import annotations

import logging
from typing import Any

from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.messaging")


def extract_message(payload: dict) -> dict | None:
    """Extract a clean message dict from a Z-API webhook payload.

    Returns None if the payload should be filtered out.
    Returns a dict with: phone, text, message_id, from_me, from_api, type, raw

    Args:
        payload: Raw Z-API webhook JSON body
    """
    # Basic structure check
    if not isinstance(payload, dict):
        return None

    return {
        "phone": payload.get("phone"),
        "text": _extract_text(payload),
        "message_id": payload.get("messageId", payload.get("id", {}).get("id", "")),
        "from_me": payload.get("fromMe", False),
        "from_api": payload.get("fromApi", False),
        "is_group": payload.get("isGroup", False),
        "is_newsletter": payload.get("isNewsletter", False),
        "is_broadcast": payload.get("broadcast", False),
        "type": payload.get("type", ""),
        "reference_message_id": payload.get("referenceMessageId", ""),
        "raw": payload,
    }


def should_filter(message: dict, config: FrameworkConfig) -> str | None:
    """Check if a message should be filtered out.

    Returns None if message should be processed.
    Returns a string reason if message should be discarded.
    """
    messaging = config.messaging

    # Filter by Z-API type (DeliveryCallback, ReadCallback, etc)
    msg_type = message.get("type", "")
    if msg_type in messaging.filter_types:
        return f"filtered_type:{msg_type}"

    # Filter non-ReceivedCallback types that aren't actual messages
    if msg_type and msg_type != "ReceivedCallback":
        # Some types we always want to filter
        always_filter = {
            "DeliveryCallback", "ReadCallback", "PresenceCallback",
            "StatusCallback", "ConnStatusCallback",
        }
        if msg_type in always_filter:
            return f"filtered_type:{msg_type}"

    # Filter groups
    if messaging.filter_groups and message.get("is_group"):
        return "filtered:group"

    # Filter newsletters
    if messaging.filter_newsletters and message.get("is_newsletter"):
        return "filtered:newsletter"

    # Filter broadcasts
    if messaging.filter_broadcasts and message.get("is_broadcast"):
        return "filtered:broadcast"

    # Filter reactions (check raw payload)
    raw = message.get("raw", {})
    if messaging.filter_reactions and "reaction" in raw:
        return "filtered:reaction"

    # Filter edits
    if messaging.filter_edits and raw.get("isEdit"):
        return "filtered:edit"

    # Filter status replies
    if raw.get("isStatusReply"):
        return "filtered:status_reply"

    # Filter waiting messages
    if raw.get("waitingMessage"):
        return "filtered:waiting_message"

    # Filter pin events
    if raw.get("pinEvent"):
        return "filtered:pin"

    # Filter events
    if raw.get("eventMessage"):
        return "filtered:event"

    # Filter payments
    if raw.get("paymentInfo"):
        return "filtered:payment"

    # Filter notifications
    if raw.get("notification"):
        return "filtered:notification"

    # Filter fromMe (messages sent by the agent itself via API)
    # BUT keep fromMe + !fromApi (human typing on agent's WhatsApp = takeover)
    if message.get("from_me") and message.get("from_api"):
        return "filtered:from_api"

    # Filter empty text
    if not message.get("text"):
        return "filtered:no_text"

    # Filter missing phone
    if not message.get("phone"):
        return "filtered:no_phone"

    return None


def is_human_takeover_message(message: dict) -> bool:
    """Detect if this is a human typing on the agent's WhatsApp.
    fromMe=true + fromApi=false = human using the phone directly."""
    return message.get("from_me", False) and not message.get("from_api", False)


def is_human_reply(message: dict, responsible_phones: list[str]) -> bool:
    """Detect if this is a human-in-the-loop reply (quote/reply from responsible).
    The responsible replies via quote on the notification message."""
    phone = message.get("phone", "")
    has_reference = bool(message.get("reference_message_id"))

    # Normalize phone (handle BR prefix 9 issue)
    normalized_responsibles = []
    for rp in responsible_phones:
        normalized_responsibles.append(rp)
        # Add variant without the 9 after DDD (55XX9... → 55XX...)
        if len(rp) == 13 and rp.startswith("55"):
            normalized_responsibles.append(rp[:4] + rp[5:])

    is_from_responsible = phone in normalized_responsibles
    return has_reference and is_from_responsible


def _extract_text(payload: dict) -> str:
    """Extract text content from various Z-API message types."""
    # Standard text message
    text = payload.get("text", {})
    if isinstance(text, dict):
        text = text.get("message", "")
    if isinstance(text, str) and text:
        return text.strip()

    # Image/video with caption
    image = payload.get("image", {})
    if isinstance(image, dict) and image.get("caption"):
        return image["caption"].strip()

    # Document with caption
    doc = payload.get("document", {})
    if isinstance(doc, dict) and doc.get("caption"):
        return doc["caption"].strip()

    # Audio (will need transcription later)
    if payload.get("audio"):
        return "[audio]"

    # Sticker
    if payload.get("sticker"):
        return "[sticker]"

    return ""
