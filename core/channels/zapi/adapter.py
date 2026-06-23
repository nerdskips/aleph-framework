"""
Aleph Framework — Z-API Channel Adapter
========================================
Parses Z-API webhook payloads into IncomingMessage objects
and implements Z-API-specific filtering and detection logic.
"""

from __future__ import annotations

import logging

from core.channels.base import IncomingMessage
from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.channels.zapi")

CHANNEL = "whatsapp"
_MEDIA_PLACEHOLDERS: frozenset[str] = frozenset({"[audio]", "[sticker]"})


class ZAPIAdapter:
    """Stateless adapter for Z-API webhook payloads.

    All methods are class methods — no state, safe to call without instantiation.
    """

    @classmethod
    def extract(cls, payload: dict) -> IncomingMessage | None:
        """Parse a Z-API webhook payload into an IncomingMessage.

        Returns None if the payload is not a valid dict.
        """
        if not isinstance(payload, dict):
            return None

        media_type, media_url, media_mimetype = cls._extract_media_meta(payload)

        return IncomingMessage(
            sender_id=payload.get("phone", ""),
            text=cls._extract_text(payload),
            message_id=payload.get("messageId", payload.get("id", {}).get("id", "")),
            channel=CHANNEL,
            is_from_agent=payload.get("fromMe", False),
            is_from_api=payload.get("fromApi", False),
            reference_message_id=payload.get("referenceMessageId", ""),
            media_type=media_type,
            media_url=media_url,
            media_mimetype=media_mimetype,
            metadata={
                "type": payload.get("type", ""),
                "is_group": payload.get("isGroup", False),
                "is_newsletter": payload.get("isNewsletter", False),
                "is_broadcast": payload.get("broadcast", False),
            },
            raw=payload,
        )

    @classmethod
    def should_filter(cls, message: IncomingMessage, config: FrameworkConfig) -> str | None:
        """Check if a message should be filtered out.

        Returns None if message should be processed.
        Returns a reason string if message should be discarded.
        """
        messaging = config.messaging
        msg_type = message.metadata.get("type", "")

        # Filter by Z-API event type (user-configured or any non-ReceivedCallback)
        if msg_type in messaging.filter_types:
            return f"filtered_type:{msg_type}"

        # Guard against unknown future event types — only ReceivedCallback is processed
        if msg_type and msg_type != "ReceivedCallback":
            return f"filtered_type:{msg_type}"

        # Filter groups / newsletters / broadcasts
        if messaging.filter_groups and message.metadata.get("is_group"):
            return "filtered:group"
        if messaging.filter_newsletters and message.metadata.get("is_newsletter"):
            return "filtered:newsletter"
        if messaging.filter_broadcasts and message.metadata.get("is_broadcast"):
            return "filtered:broadcast"

        # Filter Z-API-specific event types from raw payload
        raw = message.raw
        if messaging.filter_reactions and "reaction" in raw:
            return "filtered:reaction"
        if messaging.filter_edits and raw.get("isEdit"):
            return "filtered:edit"
        if raw.get("isStatusReply"):
            return "filtered:status_reply"
        if raw.get("waitingMessage"):
            return "filtered:waiting_message"
        if raw.get("pinEvent"):
            return "filtered:pin"
        if raw.get("eventMessage"):
            return "filtered:event"
        if raw.get("paymentInfo"):
            return "filtered:payment"
        if raw.get("notification"):
            return "filtered:notification"

        # Filter bot's own API-sent messages
        if message.is_from_agent and message.is_from_api:
            return "filtered:from_api"

        # Filter empty text — allow media through when media processing is enabled
        # Placeholder texts (e.g. "[audio]", "[sticker]") count as empty for filtering
        has_real_text = bool(message.text) and message.text not in _MEDIA_PLACEHOLDERS
        if not has_real_text:
            if config.media.enabled and message.media_type in [t.value for t in config.media.supported_types]:
                pass  # media will be processed pre-buffer
            else:
                return "filtered:no_text"

        if not message.sender_id:
            return "filtered:no_phone"

        return None

    @classmethod
    def is_human_takeover(cls, message: IncomingMessage) -> bool:
        """Detect if a human is typing directly on the agent's WhatsApp.

        fromMe=True + fromApi=False = a real person using the device.
        """
        return message.is_from_agent and not message.is_from_api

    @classmethod
    def is_human_reply(cls, message: IncomingMessage, responsible_phones: list[str]) -> bool:
        """Detect if this is a human-in-the-loop reply (quoted reply from responsible).

        Handles Brazilian phone number variants (9-digit vs 8-digit after DDD).
        """
        has_reference = bool(message.reference_message_id)

        # Normalize Brazilian phone variants
        normalized = []
        for rp in responsible_phones:
            normalized.append(rp)
            if len(rp) == 13 and rp.startswith("55"):
                normalized.append(rp[:4] + rp[5:])

        return has_reference and message.sender_id in normalized

    @classmethod
    def _extract_text(cls, payload: dict) -> str:
        """Extract text content from various Z-API message types."""
        text = payload.get("text", {})
        if isinstance(text, dict):
            text = text.get("message", "")
        if isinstance(text, str) and text:
            return text.strip()

        image = payload.get("image", {})
        if isinstance(image, dict) and image.get("caption"):
            return image["caption"].strip()

        doc = payload.get("document", {})
        if isinstance(doc, dict) and doc.get("caption"):
            return doc["caption"].strip()

        if payload.get("audio"):
            return "[audio]"

        if payload.get("sticker"):
            return "[sticker]"

        return ""

    @classmethod
    def _extract_media_meta(cls, payload: dict) -> tuple[str | None, str | None, str | None]:
        """Extract media type, URL, and mimetype from Z-API payload."""
        audio = payload.get("audio", {})
        if isinstance(audio, dict) and audio.get("audioUrl"):
            return "audio", audio["audioUrl"], audio.get("mimeType", "audio/ogg")

        image = payload.get("image", {})
        if isinstance(image, dict) and image.get("imageUrl"):
            return "image", image["imageUrl"], image.get("mimeType", "image/jpeg")

        doc = payload.get("document", {})
        if isinstance(doc, dict) and doc.get("documentUrl"):
            mimetype = doc.get("mimeType", "application/octet-stream")
            media_type = "pdf" if "pdf" in mimetype.lower() else "document"
            return media_type, doc["documentUrl"], mimetype

        return None, None, None
