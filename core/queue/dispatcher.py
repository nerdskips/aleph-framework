"""
Aleph Framework — Queue Dispatcher
=====================================
Enqueues background jobs after pipeline completion.
Fire-and-forget: errors are logged, never raised to the caller.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from core.queue.jobs import JobPayload, JobTrigger

if TYPE_CHECKING:
    from core.registry.schema import FrameworkConfig

logger = logging.getLogger("aleph.queue")

_QUEUE_KEY = "aleph:{client_id}:queue"


def _queue_key(client_id: str) -> str:
    return _QUEUE_KEY.format(client_id=client_id)


def _build_data(include_fields: list[str], result: Any, extra: dict | None = None) -> dict:
    """Extract requested fields from the pipeline result."""
    source: dict[str, Any] = {}

    # Standard pipeline result fields
    if hasattr(result, "response"):
        source["response"] = result.response
    if hasattr(result, "phone"):
        source["phone"] = result.phone
    if hasattr(result, "elapsed_seconds"):
        source["elapsed_seconds"] = result.elapsed_seconds

    # Flow collected data (if present)
    if hasattr(result, "flow_collected"):
        source["collected"] = result.flow_collected or {}

    # Last user message
    if hasattr(result, "user_message"):
        source["last_message"] = result.user_message

    if extra:
        source.update(extra)

    if not include_fields:
        return source

    return {k: source[k] for k in include_fields if k in source}


async def dispatch_jobs(
    config: FrameworkConfig,
    result: Any,
    redis_session: Any | None,
    phone: str = "",
    trigger: str = JobTrigger.PIPELINE_COMPLETE,
    extra: dict | None = None,
) -> None:
    """Enqueue all matching jobs for this trigger event.

    Args:
        config:        Agent FrameworkConfig
        result:        Pipeline result object (for field extraction)
        redis_session: RedisSession instance — if None, jobs are skipped silently
        phone:         User's phone number
        trigger:       Which event fired (pipeline_complete, flow_complete, etc.)
        extra:         Extra fields to merge into the job payload data
    """
    if not config.queue.enabled or not config.queue.jobs:
        return

    if redis_session is None:
        logger.debug("Queue enabled but Redis unavailable — skipping jobs for trigger=%s", trigger)
        return

    matching = [j for j in config.queue.jobs if j.trigger == trigger]
    if not matching:
        return

    for job_config in matching:
        if not job_config.webhook_url:
            logger.warning("Queue job has no webhook_url, skipping (trigger=%s)", trigger)
            continue

        data = _build_data(job_config.include_fields, result, extra)
        data["phone"] = phone
        data["client_id"] = config.client_id

        payload = JobPayload(
            trigger=trigger,
            client_id=config.client_id,
            phone=phone,
            webhook_url=job_config.webhook_url,
            data=data,
            timeout_seconds=job_config.timeout_seconds,
            max_retries=config.queue.max_retries,
        )

        try:
            redis = redis_session.redis  # aioredis client
            await redis.lpush(
                _queue_key(config.client_id),
                json.dumps({
                    "trigger": payload.trigger,
                    "client_id": payload.client_id,
                    "phone": payload.phone,
                    "webhook_url": payload.webhook_url,
                    "data": payload.data,
                    "timeout_seconds": payload.timeout_seconds,
                    "max_retries": payload.max_retries,
                }),
            )
            logger.info(
                "Job enqueued: trigger=%s url=%s phone=%s",
                trigger, job_config.webhook_url[:60], phone,
            )
        except Exception as e:
            logger.error("Failed to enqueue job (trigger=%s): %s", trigger, e)
