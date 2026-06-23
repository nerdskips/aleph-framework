"""
Aleph Framework — Channel Abstraction Base
==========================================
Channel-agnostic types for message input and output.

IncomingMessage — a normalized message from any channel.
ChannelSender   — abstract base for all channel-specific senders.

Adding a new channel:
  1. Create core/channels/<name>/adapter.py  — parse incoming payload → IncomingMessage
  2. Create core/channels/<name>/sender.py   — subclass ChannelSender
  3. Mount the channel's webhook route in core/api/webhooks.py
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass
class IncomingMessage:
    """Channel-agnostic representation of an incoming message.

    Attributes:
        sender_id: Who sent the message (phone number, Telegram user_id, etc.)
        text: Processed text content (may be empty for media-only messages)
        message_id: Unique ID used for anti-spam deduplication
        channel: Source channel identifier ("whatsapp", "telegram", etc.)
        is_from_agent: True if the message was sent by the agent itself
        is_from_api: True if sent via API/automation (not a real user)
        reference_message_id: ID of the quoted/replied-to message (HITL detection)
        media_type: "audio", "image", "pdf", or None
        media_url: URL to download media from
        media_mimetype: MIME type of the media
        metadata: Channel-specific extra fields (e.g. is_group, is_newsletter)
        raw: Full original payload from the channel (for channel-specific logic)
    """

    # Required
    sender_id: str
    text: str
    message_id: str
    channel: str

    # Optional with defaults
    is_from_agent: bool = False
    is_from_api: bool = False
    reference_message_id: str = ""
    media_type: str | None = None
    media_url: str | None = None
    media_mimetype: str | None = None
    metadata: dict = field(default_factory=dict)
    raw: dict = field(default_factory=dict)


class ChannelSender(ABC):
    """Abstract base class for all channel-specific message senders.

    Subclass this for each channel (Z-API/WhatsApp, Telegram, SMS, etc.)
    and implement the three abstract methods.
    """

    @abstractmethod
    async def send_response(self, recipient_id: str, text: str) -> None:
        """Send a response message to a recipient.

        Args:
            recipient_id: Destination address (phone, Telegram user_id, etc.)
            text: Message text to send
        """
        ...

    @abstractmethod
    async def send_notification(self, recipient_id: str, text: str) -> str | None:
        """Send a notification message and return its ID.

        Used for HITL escalation notifications where we need to track
        the message ID to match a quoted reply later.

        Args:
            recipient_id: Destination address
            text: Notification text

        Returns:
            Message ID string if available, None otherwise
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Close any open HTTP clients or connections."""
        ...
