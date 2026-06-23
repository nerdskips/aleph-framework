"""Tests: Phase 1 — Channel abstraction (IncomingMessage, ZAPIAdapter, ZAPISender)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.channels.base import ChannelSender, IncomingMessage
from core.channels.zapi.adapter import ZAPIAdapter
from core.channels.zapi.sender import ZAPISender
from core.registry.schema import FrameworkConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _minimal_config() -> FrameworkConfig:
    return FrameworkConfig(
        client_id="test",
        agent={"name": "Test"},
        human={"enabled": False},
    )


# ---------------------------------------------------------------------------
# IncomingMessage
# ---------------------------------------------------------------------------

def test_incoming_message_required_fields():
    msg = IncomingMessage(
        sender_id="5511999999999",
        text="hello",
        message_id="msg-001",
        channel="whatsapp",
    )
    assert msg.sender_id == "5511999999999"
    assert msg.text == "hello"
    assert msg.message_id == "msg-001"
    assert msg.channel == "whatsapp"
    assert msg.is_from_agent is False
    assert msg.is_from_api is False
    assert msg.reference_message_id == ""
    assert msg.media_type is None
    assert msg.metadata == {}
    assert msg.raw == {}


def test_incoming_message_optional_fields():
    msg = IncomingMessage(
        sender_id="123",
        text="hi",
        message_id="m1",
        channel="telegram",
        is_from_agent=True,
        reference_message_id="ref-99",
        media_type="image",
        media_url="https://example.com/img.jpg",
        metadata={"chat_id": 42},
    )
    assert msg.is_from_agent is True
    assert msg.reference_message_id == "ref-99"
    assert msg.media_type == "image"
    assert msg.metadata["chat_id"] == 42


# ---------------------------------------------------------------------------
# ZAPIAdapter.extract
# ---------------------------------------------------------------------------

def test_extract_text_message():
    payload = {
        "phone": "5511999999999",
        "messageId": "zapi-001",
        "fromMe": False,
        "fromApi": False,
        "isGroup": False,
        "isNewsletter": False,
        "broadcast": False,
        "type": "ReceivedCallback",
        "text": {"message": "Hello world"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert msg is not None
    assert msg.sender_id == "5511999999999"
    assert msg.text == "Hello world"
    assert msg.message_id == "zapi-001"
    assert msg.channel == "whatsapp"
    assert msg.is_from_agent is False
    assert msg.is_from_api is False


def test_extract_returns_none_for_non_dict():
    assert ZAPIAdapter.extract("not a dict") is None  # type: ignore
    assert ZAPIAdapter.extract(None) is None  # type: ignore


def test_extract_audio_message():
    payload = {
        "phone": "5511999999999",
        "messageId": "audio-001",
        "fromMe": False,
        "fromApi": False,
        "isGroup": False,
        "isNewsletter": False,
        "broadcast": False,
        "type": "ReceivedCallback",
        "audio": {"audioUrl": "https://example.com/audio.ogg", "mimeType": "audio/ogg"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert msg is not None
    assert msg.text == "[audio]"
    assert msg.media_type == "audio"
    assert msg.media_url == "https://example.com/audio.ogg"


def test_extract_from_api_message():
    payload = {
        "phone": "5511999999999",
        "messageId": "api-001",
        "fromMe": True,
        "fromApi": True,
        "isGroup": False,
        "isNewsletter": False,
        "broadcast": False,
        "type": "ReceivedCallback",
        "text": {"message": "sent by bot"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert msg is not None
    assert msg.is_from_agent is True
    assert msg.is_from_api is True


# ---------------------------------------------------------------------------
# ZAPIAdapter.should_filter
# ---------------------------------------------------------------------------

def test_should_filter_group():
    config = _minimal_config()
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": True, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "hi"},
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason == "filtered:group"


def test_should_filter_from_api():
    config = _minimal_config()
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": True, "fromApi": True,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "bot response"},
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason == "filtered:from_api"


def test_should_filter_no_text():
    config = _minimal_config()
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback",
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason == "filtered:no_text"


def test_should_not_filter_valid_message():
    config = _minimal_config()
    payload = {
        "phone": "5511999999999", "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "hello"},
    }
    msg = ZAPIAdapter.extract(payload)
    reason = ZAPIAdapter.should_filter(msg, config)
    assert reason is None


# ---------------------------------------------------------------------------
# ZAPIAdapter.is_human_takeover / is_human_reply
# ---------------------------------------------------------------------------

def test_is_human_takeover_from_me_not_api():
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": True, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "typing"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert ZAPIAdapter.is_human_takeover(msg) is True


def test_is_not_human_takeover_from_api():
    payload = {
        "phone": "123", "messageId": "m1", "fromMe": True, "fromApi": True,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "bot"},
    }
    msg = ZAPIAdapter.extract(payload)
    assert ZAPIAdapter.is_human_takeover(msg) is False


def test_is_human_reply_with_reference():
    payload = {
        "phone": "5534999999999",
        "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "resolved"},
        "referenceMessageId": "notif-001",
    }
    msg = ZAPIAdapter.extract(payload)
    responsible = ["5534999999999"]
    assert ZAPIAdapter.is_human_reply(msg, responsible) is True


def test_is_not_human_reply_wrong_phone():
    payload = {
        "phone": "5511000000000",
        "messageId": "m1", "fromMe": False, "fromApi": False,
        "isGroup": False, "isNewsletter": False, "broadcast": False,
        "type": "ReceivedCallback", "text": {"message": "hi"},
        "referenceMessageId": "notif-001",
    }
    msg = ZAPIAdapter.extract(payload)
    responsible = ["5534999999999"]
    assert ZAPIAdapter.is_human_reply(msg, responsible) is False


# ---------------------------------------------------------------------------
# ZAPISender
# ---------------------------------------------------------------------------

def test_zapi_sender_is_channel_sender():
    config = _minimal_config()
    sender = ZAPISender(config)
    assert isinstance(sender, ChannelSender)


async def test_zapi_sender_dry_run_does_not_call_http():
    config = _minimal_config()
    config.debug.dry_run = True
    sender = ZAPISender(config)

    with patch.object(sender, "_send_text", new_callable=AsyncMock) as mock_send:
        await sender.send_response("5511999999999", "hello")
        mock_send.assert_not_called()


async def test_zapi_sender_send_notification_returns_message_id():
    config = _minimal_config()
    config.debug.dry_run = False
    sender = ZAPISender(config)

    mock_response = {"zaapId": "abc", "messageId": "msg-returned-id"}
    with patch.object(sender, "_send_text", new_callable=AsyncMock, return_value=mock_response):
        msg_id = await sender.send_notification("5511999999999", "alert!")
        assert msg_id == "msg-returned-id"
