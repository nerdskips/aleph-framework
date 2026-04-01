"""
Tests for Phase 10 D3: background job queue.

Strategy:
- Schema tests: QueueConfig and QueueJobConfig parse correctly
- Dispatcher tests: dispatch_jobs enqueues correct payload, skips when disabled
- Worker tests: _execute_job calls webhook, handles errors
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import yaml

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_smoke_config():
    from core.registry.schema import FrameworkConfig
    config_path = Path(__file__).resolve().parents[2] / "clients" / "smoke-test" / "config.yaml"
    with open(config_path) as f:
        raw = yaml.safe_load(f)
    return FrameworkConfig(**raw)


def _make_pipeline_result(response="ok", phone="5511999999999"):
    from core.engine.pipeline import PipelineResult
    return PipelineResult(
        response=response,
        elapsed_seconds=0.5,
        user_message="test message",
        flow_collected={"name": "João"},
    )


# ---------------------------------------------------------------------------
# Schema — QueueConfig and QueueJobConfig
# ---------------------------------------------------------------------------

def test_queue_config_default_disabled():
    """QueueConfig defaults to disabled."""
    from core.registry.schema import QueueConfig
    cfg = QueueConfig()
    assert cfg.enabled is False
    assert cfg.jobs == []


def test_queue_job_config_fields():
    """QueueJobConfig parses all fields correctly."""
    from core.registry.schema import QueueJobConfig
    job = QueueJobConfig(
        trigger="pipeline_complete",
        action="webhook",
        webhook_url="http://example.com/webhook",
        include_fields=["phone", "response"],
        timeout_seconds=15,
    )
    assert job.trigger == "pipeline_complete"
    assert job.webhook_url == "http://example.com/webhook"
    assert job.timeout_seconds == 15


def test_queue_config_on_framework_config():
    """FrameworkConfig.queue defaults to QueueConfig with enabled=False."""
    config = _load_smoke_config()
    assert config.queue.enabled is False
    assert config.queue.jobs == []


# ---------------------------------------------------------------------------
# Dispatcher — dispatch_jobs
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_dispatch_jobs_skips_when_disabled():
    """dispatch_jobs does nothing when queue.enabled=False."""
    from core.queue.dispatcher import dispatch_jobs

    config = _load_smoke_config()
    config.queue.enabled = False

    mock_redis = AsyncMock()
    mock_session = MagicMock()
    mock_session.redis = mock_redis

    await dispatch_jobs(
        config=config,
        result=_make_pipeline_result(),
        redis_session=mock_session,
        phone="5511999999999",
    )

    mock_redis.lpush.assert_not_called()


@pytest.mark.asyncio
async def test_dispatch_jobs_skips_without_redis():
    """dispatch_jobs does nothing when redis_session is None."""
    from core.queue.dispatcher import dispatch_jobs
    from core.registry.schema import QueueJobConfig

    config = _load_smoke_config()
    config.queue.enabled = True
    config.queue.jobs = [
        QueueJobConfig(
            trigger="pipeline_complete",
            webhook_url="http://example.com/crm",
        )
    ]

    await dispatch_jobs(
        config=config,
        result=_make_pipeline_result(),
        redis_session=None,
        phone="5511999999999",
    )
    # No exception raised, no crash


@pytest.mark.asyncio
async def test_dispatch_jobs_enqueues_on_match():
    """dispatch_jobs enqueues a job when trigger matches."""
    import json

    from core.queue.dispatcher import dispatch_jobs
    from core.registry.schema import QueueJobConfig

    config = _load_smoke_config()
    config.queue.enabled = True
    config.queue.jobs = [
        QueueJobConfig(
            trigger="pipeline_complete",
            webhook_url="http://example.com/crm",
            include_fields=["phone", "response"],
        )
    ]

    mock_redis = AsyncMock()
    mock_session = MagicMock()
    mock_session.redis = mock_redis

    await dispatch_jobs(
        config=config,
        result=_make_pipeline_result(),
        redis_session=mock_session,
        phone="5511999999999",
        trigger="pipeline_complete",
    )

    mock_redis.lpush.assert_called_once()
    call_args = mock_redis.lpush.call_args
    queue_key = call_args[0][0]
    payload = json.loads(call_args[0][1])

    assert "aleph:" in queue_key
    assert payload["webhook_url"] == "http://example.com/crm"
    assert payload["phone"] == "5511999999999"
    assert "response" in payload["data"]


@pytest.mark.asyncio
async def test_dispatch_jobs_no_match_skips():
    """dispatch_jobs does not enqueue when trigger doesn't match any job."""
    from core.queue.dispatcher import dispatch_jobs
    from core.registry.schema import QueueJobConfig

    config = _load_smoke_config()
    config.queue.enabled = True
    config.queue.jobs = [
        QueueJobConfig(
            trigger="flow_complete",   # only fires on flow_complete
            flow_id="pedido",
            webhook_url="http://example.com/orders",
        )
    ]

    mock_redis = AsyncMock()
    mock_session = MagicMock()
    mock_session.redis = mock_redis

    await dispatch_jobs(
        config=config,
        result=_make_pipeline_result(),
        redis_session=mock_session,
        phone="5511999999999",
        trigger="pipeline_complete",  # different trigger
    )

    mock_redis.lpush.assert_not_called()


# ---------------------------------------------------------------------------
# Worker — _execute_job
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_execute_job_success():
    """_execute_job returns True on successful webhook call."""
    from core.queue.worker import _execute_job

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _execute_job({
            "webhook_url": "http://example.com/hook",
            "data": {"phone": "5511999999999"},
            "timeout_seconds": 5,
        })

    assert result is True


@pytest.mark.asyncio
async def test_execute_job_no_url():
    """_execute_job returns False when webhook_url is empty."""
    from core.queue.worker import _execute_job

    result = await _execute_job({"webhook_url": "", "data": {}})
    assert result is False


@pytest.mark.asyncio
async def test_execute_job_timeout():
    """_execute_job returns False on timeout."""
    import httpx

    from core.queue.worker import _execute_job

    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.TimeoutException("timed out"))
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)

        result = await _execute_job({
            "webhook_url": "http://example.com/hook",
            "data": {},
            "timeout_seconds": 1,
        })

    assert result is False
