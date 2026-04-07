"""
Aleph Framework — Image Description via Vision API
====================================================
Sends image URL to a vision-capable model and returns a text description.
Lazy-imports openai to avoid hard dependency when media is disabled.
"""

from __future__ import annotations

import logging
import os

from core.registry.schema import MediaConfig

logger = logging.getLogger("aleph.media.vision")


async def describe_image(url: str, config: MediaConfig) -> str:
    """Send image URL to vision model and return text description.

    Args:
        url: Public image URL from Z-API
        config: MediaConfig with image_model and image_prompt

    Returns:
        Text description of the image. Empty string on failure.
    """
    from openai import AsyncOpenAI  # lazy: optional dep

    # Note: max_file_size_mb is not enforced here — the image URL is passed
    # directly to the vision API without downloading, so byte size is unavailable.
    # Z-API already limits media to 16MB for WhatsApp messages.
    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    try:
        response = await client.chat.completions.create(
            model=config.image_model,
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": config.image_prompt},
                        {"type": "image_url", "image_url": {"url": url, "detail": "auto"}},
                    ],
                }
            ],
            max_tokens=500,
        )
        text = response.choices[0].message.content or ""
        logger.info("Image described: %d chars", len(text))
        return text
    except Exception as e:
        logger.error("Vision API call failed for %s: %s", url[:80], str(e)[:200])
        return ""
