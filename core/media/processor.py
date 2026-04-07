"""
Aleph Framework — Media Processor
===================================
Dispatches media processing based on message media_type.
Entry point called from webhooks.py before the message buffer.
"""

from __future__ import annotations

import logging

from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.media")


async def process_media(message: dict, config: FrameworkConfig) -> str:
    """Process a media message and return its text representation.

    Dispatches to the appropriate handler based on message["media_type"].
    Returns empty string for unsupported types or on error (non-fatal).

    Args:
        message: Enriched message dict from extract_message() with media_type/media_url fields
        config: Full FrameworkConfig (uses config.media)

    Returns:
        Processed text to replace the original message text in the pipeline.
    """
    media_cfg = config.media
    media_type = message.get("media_type")
    media_url = message.get("media_url", "")
    supported = {t.value for t in media_cfg.supported_types}

    if not media_url:
        logger.warning("Media message missing URL (type=%s), skipping", media_type)
        return ""

    if media_type not in supported:
        logger.debug("Media type '%s' not in supported_types %s, skipping", media_type, supported)
        return ""

    try:
        if media_type == "audio":
            from core.media.audio import transcribe_audio

            text = await transcribe_audio(media_url, media_cfg)
            return f"[Áudio transcrito]: {text}" if text else ""

        if media_type == "image":
            from core.media.vision import describe_image

            description = await describe_image(media_url, media_cfg)
            caption = message.get("text", "")
            if caption and not caption.startswith("["):
                return f"[Imagem — legenda: {caption}]\n[Descrição]: {description}"
            return f"[Imagem]: {description}"

        if media_type == "pdf":
            from core.media.pdf import extract_pdf_text

            text = await extract_pdf_text(media_url, media_cfg)
            caption = message.get("text", "")
            prefix = f"[Documento: {caption}]\n" if caption and not caption.startswith("[") else "[Documento PDF]:\n"
            return prefix + text

        logger.debug("No handler for media_type '%s'", media_type)
        return ""

    except Exception as e:
        logger.error("Media processing failed (type=%s url=%s): %s", media_type, media_url[:80], str(e)[:200])
        return ""
