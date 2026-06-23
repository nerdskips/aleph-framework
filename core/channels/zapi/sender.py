"""
Aleph Framework — Z-API Message Sender
===============================================
Implements ChannelSender for WhatsApp via Z-API.

Sends messages with humanized delivery:
  - Splits response by paragraph into multiple messages
  - Random delay between messages (human-like typing)
  - Optional disclaimer at the end

Environment:
  ZAPI_INSTANCE      — Z-API instance ID
  ZAPI_TOKEN         — Z-API instance token
  ZAPI_CLIENT_TOKEN  — Z-API client token
  ZAPI_BASE_URL      — Z-API base URL (default: https://api.z-api.io/instances)
"""

from __future__ import annotations

import asyncio
import logging
import os
import random

import httpx

from core.channels.base import ChannelSender
from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.channels.zapi")


class ZAPISender(ChannelSender):
    """Sends messages via Z-API with humanized delivery."""

    def __init__(self, config: FrameworkConfig):
        self.config = config
        self._http: httpx.AsyncClient | None = None

    @property
    def base_url(self) -> str:
        base = os.environ.get("ZAPI_BASE_URL", "https://api.z-api.io/instances")
        instance = os.environ.get("ZAPI_INSTANCE", "")
        token = os.environ.get("ZAPI_TOKEN", "")
        return f"{base}/{instance}/token/{token}"

    @property
    def headers(self) -> dict:
        return {
            "Client-Token": os.environ.get("ZAPI_CLIENT_TOKEN", ""),
            "Content-Type": "application/json",
        }

    @property
    def http(self) -> httpx.AsyncClient:
        if self._http is None or self._http.is_closed:
            self._http = httpx.AsyncClient(timeout=30.0)
        return self._http

    async def close(self) -> None:
        if self._http and not self._http.is_closed:
            await self._http.aclose()

    async def send_response(self, recipient_id: str, text: str) -> None:
        """Send agent response with humanized delivery.

        Splits by paragraph, sends each as separate message with
        random delay. Appends disclaimer if enabled.
        """
        messaging = self.config.messaging

        if self.config.debug.dry_run:
            logger.info("[DRY RUN] Would send to %s: %s", recipient_id, text[:100])
            return

        parts = [p.strip() for p in text.split("\n") if p.strip()] if messaging.send_as_paragraphs else [text]

        if messaging.disclaimer.enabled:
            disclaimer = f"{messaging.disclaimer.separator}{messaging.disclaimer.text}"
            if parts:
                parts[-1] += disclaimer
            else:
                parts = [disclaimer]

        for i, part in enumerate(parts):
            await self._send_text(recipient_id, part)
            if i < len(parts) - 1:
                delay_ms = random.randint(messaging.delay_min_ms, messaging.delay_max_ms)
                await asyncio.sleep(delay_ms / 1000.0)

        logger.info("Sent %d message(s) to %s (%d chars total)", len(parts), recipient_id, len(text))

    async def send_notification(self, recipient_id: str, text: str) -> str | None:
        """Send a notification message. Returns the messageId for quote tracking."""
        if self.config.debug.dry_run:
            logger.info("[DRY RUN] Notification to %s: %s", recipient_id, text[:100])
            return None

        result = await self._send_text(recipient_id, text)
        if result:
            return result.get("messageId")
        return None

    async def _send_text(self, recipient_id: str, text: str) -> dict | None:
        """Send a single text message via Z-API."""
        url = f"{self.base_url}/send-text"
        payload = {"phone": recipient_id, "message": text}

        try:
            response = await self.http.post(url, json=payload, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            logger.debug("Z-API sent to %s: %s", recipient_id, text[:50])
            return data
        except httpx.HTTPError as e:
            logger.error("Z-API send failed to %s: %s", recipient_id, str(e)[:200])
            return None
