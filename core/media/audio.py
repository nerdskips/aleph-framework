"""
Aleph Framework — Audio Transcription
======================================
Transcribes WhatsApp audio messages via OpenAI Whisper API.
Audio URL is downloaded first, then submitted to Whisper.
Lazy-imports openai to avoid hard dependency when media is disabled.
"""

from __future__ import annotations

import io
import logging

import httpx

from core.registry.schema import MediaConfig

logger = logging.getLogger("aleph.media.audio")


async def transcribe_audio(url: str, config: MediaConfig) -> str:
    """Download audio from URL and transcribe via Whisper.

    Args:
        url: Direct audio URL from Z-API
        config: MediaConfig with audio_model and max_file_size_mb

    Returns:
        Transcribed text string. Empty string on failure.
    """
    import os

    from openai import AsyncOpenAI  # lazy: optional dep

    # Download audio
    async with httpx.AsyncClient(timeout=30.0) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        audio_bytes = resp.content

    size_mb = len(audio_bytes) / (1024 * 1024)
    if size_mb > config.max_file_size_mb:
        logger.warning("Audio file too large (%.1fMB > %dMB limit), skipping", size_mb, config.max_file_size_mb)
        return ""

    # Whisper needs a file-like with a name for format detection
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "audio.ogg"

    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))
    result = await client.audio.transcriptions.create(
        model=config.audio_model,
        file=audio_file,
        response_format="text",
    )
    text = result.strip() if isinstance(result, str) else getattr(result, "text", str(result)).strip()
    logger.info("Audio transcribed: %d chars", len(text))
    return text
