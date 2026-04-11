"""
Aleph Framework — Queue Worker
=================================
BRPOP consumer that executes background jobs from the Redis queue.

Usage:
  python -m core.queue.worker --client <name>

The worker runs as a long-lived process alongside the webhook server.
Each job is a webhook POST — fire-and-forget with retry logic.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.queue.worker")

_QUEUE_KEY = "aleph:{client_id}:queue"


async def _execute_job(job: dict[str, Any]) -> bool:
    """Execute a single job. Returns True on success."""
    import httpx

    url = job.get("webhook_url", "")
    data = job.get("data", {})
    timeout = job.get("timeout_seconds", 10)

    if not url:
        logger.error("Job has no webhook_url, dropping")
        return False

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(url, json=data)
            resp.raise_for_status()
            logger.info("Job executed: url=%s status=%d", url[:60], resp.status_code)
            return True
    except httpx.TimeoutException:
        logger.warning("Job timed out: url=%s", url[:60])
        return False
    except Exception as e:
        logger.warning("Job failed: url=%s error=%s", url[:60], e)
        return False


async def run_worker(config: FrameworkConfig, redis: Any) -> None:
    """Long-running BRPOP worker loop.

    Consumes jobs from aleph:{client_id}:queue and executes them.
    Retries up to max_retries times before dropping.

    Args:
        config: Agent FrameworkConfig (for client_id and retry settings)
        redis:  aioredis client instance
    """
    queue_key = _QUEUE_KEY.format(client_id=config.client_id)
    max_retries = config.queue.max_retries
    retry_delay = config.queue.retry_delay_seconds

    logger.info("Queue worker started: key=%s max_retries=%d", queue_key, max_retries)

    while True:
        try:
            # BRPOP blocks up to 5s then loops — allows graceful shutdown
            item = await redis.brpop(queue_key, timeout=5)
            if item is None:
                continue

            _, raw = item
            job = json.loads(raw)
            job_retries = min(job.get("max_retries", max_retries), max_retries)

            success = False
            for attempt in range(1, job_retries + 1):
                success = await _execute_job(job)
                if success:
                    break
                if attempt < job_retries:
                    logger.info("Retrying job (attempt %d/%d)", attempt + 1, job_retries)
                    await asyncio.sleep(retry_delay)

            if not success:
                logger.error(
                    "Job dropped after %d retries: url=%s",
                    job_retries, job.get("webhook_url", "")[:60],
                )

        except asyncio.CancelledError:
            logger.info("Queue worker cancelled, shutting down")
            break
        except Exception as e:
            logger.error("Worker loop error: %s", e)
            await asyncio.sleep(1)  # brief pause before retrying the loop
