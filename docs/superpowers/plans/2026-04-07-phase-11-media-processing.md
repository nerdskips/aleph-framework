# Phase 11 — Media Processing

> **Status:** In progress — branch `feature/phase-11-media-processing`
>
> **Goal:** Add audio transcription (Whisper), image understanding (Vision), and PDF text extraction to the pipeline. DEFAULT OFF per client. Zero changes to pipeline logic — media is processed before it enters the buffer so the rest of the stack sees plain text.

---

## Architecture

```
Z-API webhook
  → extract_message() — enrich with media_url, media_type, media_mimetype
  → should_filter() — allow media through when media.enabled + type supported
  → webhooks.py pre-buffer step:
      if media.enabled and message has media:
          text = await process_media(message, config)
          replace message["text"] with transcription/description/extracted text
  → buffer (now has real text, not "[audio]")
  → pipeline (unchanged)
```

Media processing is **pre-buffer**. The rest of the stack (pipeline, runner, guardrails, episodic memory) never knows it was a media message — it just sees text.

---

## Files

| Action | File | Responsibility |
|---|---|---|
| MODIFY | `core/messaging/zapi_filter.py` | `extract_message` adds `media_type`, `media_url`, `media_base64`, `media_mimetype`; `should_filter` allows media through |
| CREATE | `core/media/audio.py` | `transcribe_audio(url, config) -> str` via Whisper API |
| CREATE | `core/media/vision.py` | `describe_image(url, config) -> str` via Vision API |
| CREATE | `core/media/pdf.py` | `extract_pdf_text(url, config) -> str` via pypdf (lazy import) |
| CREATE | `core/media/processor.py` | `process_media(message, config) -> str` dispatcher |
| MODIFY | `core/media/__init__.py` | Re-export `process_media` |
| MODIFY | `core/api/webhooks.py` | Pre-buffer media processing step |
| MODIFY | `core/registry/schema.py` | Extend `MediaConfig`: `pdf_max_pages`, `image_prompt` |
| CREATE | `tests/framework/test_media.py` | Unit tests for each processor + dispatcher |

---

## Schema additions to `MediaConfig`

```python
class MediaConfig(BaseModel):
    enabled: bool = Field(False)
    supported_types: list[MediaType] = Field(default_factory=list)
    audio_model: str = Field("whisper-1", description="Whisper model for transcription")
    image_model: str = Field("gpt-4o-mini", description="Vision model")
    image_prompt: str = Field(
        "Descreva o conteúdo desta imagem de forma concisa e objetiva.",
        description="System prompt for image description",
    )
    max_file_size_mb: int = Field(25, ge=1, description="Max file size to process")
    pdf_max_pages: int = Field(10, ge=1, le=100, description="Max PDF pages to extract")
```

---

## `extract_message` additions

Z-API media payload shapes:
```json
// audio
{"audio": {"audioUrl": "https://...", "mimeType": "audio/ogg"}}

// image
{"image": {"imageUrl": "https://...", "caption": "...", "mimeType": "image/jpeg"}}

// document/PDF
{"document": {"documentUrl": "https://...", "caption": "...", "mimeType": "application/pdf", "fileName": "file.pdf"}}
```

Add to returned message dict:
```python
"media_type": "audio" | "image" | "document" | None,
"media_url": str | None,
"media_mimetype": str | None,
```

---

## `core/media/audio.py`

```python
"""Aleph Framework — Audio transcription via Whisper."""

from __future__ import annotations

import logging
import httpx
from core.registry.schema import MediaConfig

logger = logging.getLogger("aleph.media.audio")

async def transcribe_audio(url: str, config: MediaConfig) -> str:
    """Download audio from URL and transcribe via Whisper API."""
    import os
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        audio_bytes = resp.content

    # Whisper requires a file-like with a name hint for format detection
    import io
    audio_file = io.BytesIO(audio_bytes)
    audio_file.name = "audio.ogg"

    result = await client.audio.transcriptions.create(
        model=config.audio_model,
        file=audio_file,
        response_format="text",
    )
    return result.strip() if isinstance(result, str) else result.text.strip()
```

---

## `core/media/vision.py`

```python
"""Aleph Framework — Image description via Vision API."""

from __future__ import annotations

import logging
import os
from core.registry.schema import MediaConfig

logger = logging.getLogger("aleph.media.vision")

async def describe_image(url: str, config: MediaConfig) -> str:
    """Describe image content via Vision API."""
    from openai import AsyncOpenAI

    client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY", ""))

    response = await client.chat.completions.create(
        model=config.image_model,
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": config.image_prompt},
                    {"type": "image_url", "image_url": {"url": url}},
                ],
            }
        ],
        max_tokens=500,
    )
    return response.choices[0].message.content or ""
```

---

## `core/media/pdf.py`

```python
"""Aleph Framework — PDF text extraction."""

from __future__ import annotations

import logging
import httpx
from core.registry.schema import MediaConfig

logger = logging.getLogger("aleph.media.pdf")

async def extract_pdf_text(url: str, config: MediaConfig) -> str:
    """Download PDF and extract text (pypdf, lazy import)."""
    try:
        import pypdf  # lazy: optional dependency
    except ImportError:
        logger.warning("pypdf not installed — PDF processing unavailable. pip install pypdf")
        return "[PDF não pôde ser processado: pypdf não instalado]"

    import io
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(url)
        resp.raise_for_status()
        pdf_bytes = resp.content

    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    pages = reader.pages[: config.pdf_max_pages]
    text = "\n\n".join(
        page.extract_text() or "" for page in pages
    ).strip()

    if not text:
        return "[PDF sem texto extraível]"

    truncated = len(reader.pages) > config.pdf_max_pages
    suffix = f"\n\n[... {len(reader.pages) - config.pdf_max_pages} página(s) omitida(s)]" if truncated else ""
    return text + suffix
```

---

## `core/media/processor.py`

```python
"""Aleph Framework — Media processor dispatcher."""

from __future__ import annotations

import logging
from core.registry.schema import FrameworkConfig, MediaType

logger = logging.getLogger("aleph.media")

async def process_media(message: dict, config: FrameworkConfig) -> str:
    """Dispatch media processing based on message media_type.

    Returns processed text. Returns "" on unsupported type or error.
    """
    media_cfg = config.media
    media_type = message.get("media_type")
    media_url = message.get("media_url", "")

    if not media_url:
        logger.warning("Media message has no URL, skipping processing")
        return ""

    supported = {t.value for t in media_cfg.supported_types}

    try:
        if media_type == MediaType.AUDIO and MediaType.AUDIO.value in supported:
            from core.media.audio import transcribe_audio
            text = await transcribe_audio(media_url, media_cfg)
            logger.info("Audio transcribed: %d chars", len(text))
            return f"[Transcrição de áudio]: {text}"

        if media_type == MediaType.IMAGE and MediaType.IMAGE.value in supported:
            from core.media.vision import describe_image
            caption = message.get("text", "")
            text = await describe_image(media_url, media_cfg)
            if caption:
                return f"[Imagem — legenda: {caption}]\n[Descrição]: {text}"
            return f"[Imagem]: {text}"

        if media_type == MediaType.PDF and MediaType.PDF.value in supported:
            from core.media.pdf import extract_pdf_text
            text = await extract_pdf_text(media_url, media_cfg)
            caption = message.get("text", "")
            prefix = f"[Documento: {caption}]\n" if caption else "[Documento PDF]:\n"
            return prefix + text

        logger.debug("Media type '%s' not in supported_types, skipping", media_type)
        return ""

    except Exception as e:
        logger.error("Media processing failed (type=%s): %s", media_type, str(e)[:200])
        return ""
```

---

## `webhooks.py` changes

In `_process_after_buffer`, after `consolidated = await _redis.consume_buffer(phone)`:

```python
# Pre-pipeline media processing
if _registry.config.media.enabled and consolidated:
    # Check if original raw message had media (stored in buffer metadata — or re-check last raw)
    pass  # handled below via message object
```

Actually cleaner to do it BEFORE buffering in `webhook_zapi`:

After the anti-spam check, before `await _redis.buffer_message(phone, text)`:
```python
# Media processing — pre-buffer, replaces placeholder text
if _registry.config.media.enabled and message.get("media_type"):
    from core.media.processor import process_media
    processed = await process_media(message, _registry.config)
    if processed:
        text = processed
        message["text"] = processed
    elif not text or text.startswith("["):
        # Unsupported or failed and no caption fallback — filter
        return JSONResponse({"status": "filtered", "reason": "media_unsupported"})
```

---

## Task breakdown

### Task 1 — Schema extension + `zapi_filter` media enrichment
### Task 2 — `core/media/audio.py` + `core/media/vision.py` + `core/media/pdf.py`
### Task 3 — `core/media/processor.py` + `__init__.py`
### Task 4 — Wire into `webhooks.py` pre-buffer
### Task 5 — Tests
### Task 6 — Docs + example config
