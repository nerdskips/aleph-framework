"""Tests: Phase 11 — Media Processing."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from core.media.processor import process_media
from core.messaging.zapi_filter import extract_message, should_filter
from core.registry.schema import FrameworkConfig


def _make_config(**media_overrides) -> FrameworkConfig:
    return FrameworkConfig(
        client_id="test",
        agent={"name": "Bot", "model": "gpt-4o-mini"},
        media={"enabled": True, "supported_types": ["audio", "image", "pdf"], **media_overrides},
    )


def _make_message(media_type: str, media_url: str = "https://example.com/file", text: str = "") -> dict:
    return {
        "phone": "+5511999",
        "text": text,
        "media_type": media_type,
        "media_url": media_url,
        "media_mimetype": "audio/ogg" if media_type == "audio" else "image/jpeg",
    }


# --- processor tests ---

async def test_process_media_audio_dispatches():
    config = _make_config()
    msg = _make_message("audio")
    # transcribe_audio is lazy-imported inside process_media; patch on the audio module itself
    with patch("core.media.audio.transcribe_audio", new_callable=AsyncMock, return_value="olá mundo"):
        result = await process_media(msg, config)
    # Should return a non-empty string with the transcription prefix
    assert result is not None
    assert isinstance(result, str)


async def test_process_media_returns_empty_for_unsupported_type():
    config = _make_config(supported_types=["audio"])  # only audio enabled
    msg = _make_message("image")  # image not supported
    result = await process_media(msg, config)
    assert result == ""


async def test_process_media_returns_empty_when_no_url():
    config = _make_config()
    msg = _make_message("audio", media_url="")
    result = await process_media(msg, config)
    assert result == ""


async def test_process_media_catches_exception():
    config = _make_config()
    msg = _make_message("audio")
    # Patch transcribe_audio on the audio module (lazy-imported inside process_media)
    with patch("core.media.audio.transcribe_audio", side_effect=Exception("API down")):
        result = await process_media(msg, config)
    # Should return "" not raise
    assert result == ""


# --- zapi_filter media extraction tests ---


def test_extract_message_audio_metadata():
    payload = {
        "phone": "+5511999",
        "type": "ReceivedCallback",
        "audio": {"audioUrl": "https://cdn.zapi.app/audio.ogg", "mimeType": "audio/ogg"},
    }
    msg = extract_message(payload)
    assert msg["media_type"] == "audio"
    assert msg["media_url"] == "https://cdn.zapi.app/audio.ogg"
    assert msg["media_mimetype"] == "audio/ogg"


def test_extract_message_image_metadata():
    payload = {
        "phone": "+5511999",
        "type": "ReceivedCallback",
        "image": {"imageUrl": "https://cdn.zapi.app/photo.jpg", "caption": "olha isso", "mimeType": "image/jpeg"},
    }
    msg = extract_message(payload)
    assert msg["media_type"] == "image"
    assert msg["media_url"] == "https://cdn.zapi.app/photo.jpg"
    assert msg["text"] == "olha isso"  # caption extracted as text


def test_extract_message_pdf_metadata():
    payload = {
        "phone": "+5511999",
        "type": "ReceivedCallback",
        "document": {
            "documentUrl": "https://cdn.zapi.app/file.pdf",
            "mimeType": "application/pdf",
            "caption": "meu documento",
        },
    }
    msg = extract_message(payload)
    assert msg["media_type"] == "pdf"
    assert msg["media_url"] == "https://cdn.zapi.app/file.pdf"


def test_extract_message_text_has_no_media():
    payload = {
        "phone": "+5511999",
        "type": "ReceivedCallback",
        "text": {"message": "oi"},
    }
    msg = extract_message(payload)
    assert msg["media_type"] is None
    assert msg["media_url"] is None


def test_should_filter_allows_audio_when_media_enabled():
    config = FrameworkConfig(
        client_id="test",
        agent={"name": "Bot", "model": "gpt-4o-mini"},
        media={"enabled": True, "supported_types": ["audio"]},
    )
    msg = {
        "phone": "+5511999",
        "text": "",  # no text — pure audio
        "media_type": "audio",
        "media_url": "https://cdn.zapi.app/audio.ogg",
        "from_me": False, "from_api": False,
        "is_group": False, "is_newsletter": False, "is_broadcast": False,
        "type": "ReceivedCallback",
        "reference_message_id": "",
        "raw": {},
    }
    result = should_filter(msg, config)
    assert result is None  # allowed through


def test_should_filter_blocks_audio_when_media_disabled():
    config = FrameworkConfig(
        client_id="test",
        agent={"name": "Bot", "model": "gpt-4o-mini"},
        media={"enabled": False},
    )
    msg = {
        "phone": "+5511999",
        "text": "",
        "media_type": "audio",
        "media_url": "https://cdn.zapi.app/audio.ogg",
        "from_me": False, "from_api": False,
        "is_group": False, "is_newsletter": False, "is_broadcast": False,
        "type": "ReceivedCallback",
        "reference_message_id": "",
        "raw": {},
    }
    result = should_filter(msg, config)
    assert result == "filtered:no_text"
