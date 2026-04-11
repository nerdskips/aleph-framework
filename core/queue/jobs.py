"""
Aleph Framework — Queue Job Types
==================================
Defines the payload and trigger types for background jobs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class JobTrigger(str, Enum):
    """Events that can trigger a background job."""
    PIPELINE_COMPLETE = "pipeline_complete"   # after every successful agent response
    FLOW_COMPLETE = "flow_complete"           # when a flow reaches on_complete
    ESCALATION_START = "escalation_start"     # when human escalation begins


@dataclass
class JobPayload:
    """Serializable payload sent to the queue worker."""
    trigger: str
    client_id: str
    phone: str
    webhook_url: str
    data: dict[str, Any] = field(default_factory=dict)
    timeout_seconds: int = 10
    max_retries: int = 3
