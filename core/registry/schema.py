"""
Zuper Agent Framework — Config Schema
======================================
Pydantic models that define the exact shape of config.yaml.
Every field, type, default, and validation lives here.

Each core module has a corresponding config section:
  agent       → core/engine/
  guardrails  → core/guardrails/
  session     → core/session/
  human       → core/human/
  habits      → core/habits/
  messaging   → core/messaging/
  llm         → core/llm/
  media       → core/media/
  queue       → core/queue/
  api         → core/api/
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GuardrailAction(str, Enum):
    """What to do when an input guardrail matches."""
    ESCALATE = "escalate"           # força escalonamento (com ou sem hábito)
    ESCALATE_NO_HABIT = "escalate_no_habit"  # sempre escalonar, nunca usar hábito
    TAKEOVER = "takeover"           # transbordo direto
    REDIRECT = "redirect"           # redireciona com mensagem custom
    TOOL_REQUIRED = "tool_required" # força tool_choice = required
    INJECT = "inject"               # injeta instrução extra no input
    BLOCK = "block"                 # bloqueia e responde com frase segura
    BYPASS_LLM = "bypass_llm"      # cálculo mecânico, pula o LLM
    CONTINUE = "continue"           # segue pro LLM normalmente


class OutputGuardrailType(str, Enum):
    """Types of output guardrail checks."""
    FABRICATION = "fabrication"         # endereço, dados internos, unidades inventadas
    PRICE_LEAK = "price_leak"          # preço solto fora de contexto
    GHOST_ESCALATION = "ghost_escalation"  # LLM diz que vai escalonar mas não chamou tool
    CUSTOM_REGEX = "custom_regex"      # pattern customizado pelo cliente


class MediaType(str, Enum):
    """Supported media types for processing."""
    AUDIO = "audio"
    IMAGE = "image"
    PDF = "pdf"


# ---------------------------------------------------------------------------
# Agent — core identity and behavior
# ---------------------------------------------------------------------------

class AgentConfig(BaseModel):
    """Core agent identity. The minimum a client must configure."""
    name: str = Field(..., description="Agent display name (e.g. 'Laura', 'Echo Bot')")
    description: str = Field("", description="Short description for tracing/logs")
    model: str = Field("openai/gpt-4.1-mini", description="Primary model via Bifrost")
    fallback_model: str = Field("google/gemini-2.5-flash", description="Fallback if primary fails")
    temperature: float = Field(0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(1024, ge=1)
    system_prompt_file: str = Field("prompts/system.md", description="Path relative to client dir")


# ---------------------------------------------------------------------------
# Guardrails — input classification + output validation
# ---------------------------------------------------------------------------

class InputPattern(BaseModel):
    """A single input guardrail pattern rule."""
    name: str = Field(..., description="Rule identifier (e.g. 'greeting', 'escalation')")
    keywords: list[str] = Field(default_factory=list, description="Keyword matches (lowercased, normalized)")
    regex: list[str] = Field(default_factory=list, description="Regex patterns (applied on normalized text)")
    action: GuardrailAction = Field(GuardrailAction.CONTINUE)
    tool_choice: str = Field("auto", description="'auto' or 'required'")
    inject_instruction: str = Field("", description="Extra instruction injected into LLM input")
    redirect_message: str = Field("", description="Message sent when action=redirect")
    priority: int = Field(0, description="Higher priority patterns are evaluated first")

    @field_validator("regex", mode="before")
    @classmethod
    def validate_regex(cls, v: list[str]) -> list[str]:
        import re
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid regex '{pattern}': {e}")
        return v


class OutputGuardrailRule(BaseModel):
    """A single output guardrail rule."""
    name: str
    type: OutputGuardrailType
    patterns: list[str] = Field(default_factory=list, description="Regex patterns to detect")
    exempt_intents: list[str] = Field(default_factory=list, description="Input intents that skip this rule")
    safe_response: str = Field(
        "Desculpe, não tenho essa informação. Posso verificar com a equipe?",
        description="Replacement message when output is blocked"
    )
    enabled: bool = True


class GuardrailsConfig(BaseModel):
    """Input + output guardrail configuration."""
    input_patterns: list[InputPattern] = Field(default_factory=list)
    output_rules: list[OutputGuardrailRule] = Field(default_factory=list)
    normalize_accents: bool = Field(True, description="Remove accents before pattern matching")
    normalize_lowercase: bool = Field(True, description="Lowercase before pattern matching")


# ---------------------------------------------------------------------------
# Session — Redis state management
# ---------------------------------------------------------------------------

class SessionConfig(BaseModel):
    """Redis session configuration. Defaults from Laura production values."""
    buffer_timeout: int = Field(8, ge=1, le=60, description="Seconds to wait for chunked messages")
    antispam_ttl: int = Field(120, ge=10, description="TTL in seconds for messageId dedup")
    history_ttl: int = Field(10800, ge=60, description="Conversation history TTL (default 3h)")
    history_max: int = Field(50, ge=5, description="Max messages kept in history")
    processing_lock_ttl: int = Field(30, ge=5, description="Lock per phone to avoid duplicate responses")
    context_ttl: int = Field(10800, ge=60, description="Client context (name, bairro) TTL")


# ---------------------------------------------------------------------------
# Human-in-the-loop — escalation + takeover
# ---------------------------------------------------------------------------

class HumanConfig(BaseModel):
    """Human-in-the-loop and takeover configuration."""
    enabled: bool = Field(True)
    escalation_session_ttl: int = Field(7200, ge=60, description="Paused session TTL (default 2h)")
    takeover_lock_ttl: int = Field(3600, ge=60, description="Takeover lock duration (default 1h)")
    takeover_renew_on_message: bool = Field(True, description="Renew lock on each human message")
    release_keyword: str = Field("RELEASE", description="Keyword to release takeover")
    notify_via: str = Field("zapi", description="Notification channel: 'zapi' or 'n8n'")
    responsible_phones: list[str] = Field(default_factory=list, description="Phone numbers to notify")
    lid_mapping_ttl: int = Field(86400, ge=3600, description="LID↔phone cache TTL (default 24h)")


# ---------------------------------------------------------------------------
# Habits — operational learning
# ---------------------------------------------------------------------------

class HabitsConfig(BaseModel):
    """Operational habits (hybrid search + dedup)."""
    enabled: bool = Field(True)
    embedding_model: str = Field("openai/text-embedding-3-small", description="Via Bifrost")
    embedding_dimensions: int = Field(1536)
    dedup_threshold: float = Field(0.020, ge=0.0, le=1.0, description="RRF score threshold for dedup")
    rrf_k: int = Field(60, ge=1, description="RRF ranking constant")
    match_count: int = Field(3, ge=1, description="Max results from hybrid search")
    search_before_escalate: bool = Field(True, description="Check habits before escalating")
    table_name: str = Field("operational_habits")


# ---------------------------------------------------------------------------
# Messaging — WhatsApp I/O
# ---------------------------------------------------------------------------

class DisclaimerConfig(BaseModel):
    """Disclaimer appended to agent messages."""
    enabled: bool = True
    text: str = "Agente de experimentação Zuper"
    separator: str = "\n\n---\n"


class MessagingConfig(BaseModel):
    """Z-API messaging configuration."""
    provider: str = Field("zapi", description="Messaging provider (zapi for now)")
    send_as_paragraphs: bool = Field(True, description="Split response into multiple messages")
    delay_min_ms: int = Field(800, ge=0, description="Min delay between messages")
    delay_max_ms: int = Field(2500, ge=0, description="Max delay between messages")
    disclaimer: DisclaimerConfig = Field(default_factory=DisclaimerConfig)
    # Z-API filter: which webhook types to ignore
    filter_types: list[str] = Field(
        default=[
            "DeliveryCallback", "ReadCallback", "PresenceCallback",
            "StatusCallback", "ConnStatusCallback",
        ],
        description="Z-API webhook types to silently discard"
    )
    filter_groups: bool = Field(True, description="Ignore group messages")
    filter_newsletters: bool = Field(True, description="Ignore newsletter messages")
    filter_broadcasts: bool = Field(True, description="Ignore broadcast messages")
    filter_reactions: bool = Field(True, description="Ignore reaction messages")
    filter_edits: bool = Field(True, description="Ignore edited messages")


# ---------------------------------------------------------------------------
# Follow-up — proactive re-engagement
# ---------------------------------------------------------------------------

class FollowUpStep(BaseModel):
    """One step in the follow-up sequence."""
    delay_minutes: int = Field(..., ge=1)
    message: str = Field(..., description="Message template (supports {name} placeholder)")


class FollowUpConfig(BaseModel):
    """Proactive follow-up after inactivity."""
    enabled: bool = Field(False)
    steps: list[FollowUpStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Media — audio, image, PDF processing
# ---------------------------------------------------------------------------

class MediaConfig(BaseModel):
    """Media processing configuration."""
    enabled: bool = Field(False)
    supported_types: list[MediaType] = Field(default_factory=list)
    audio_model: str = Field("whisper-1", description="Whisper model for transcription")
    image_model: str = Field("openai/gpt-4o-mini", description="Vision model via Bifrost")
    max_file_size_mb: int = Field(25, ge=1, description="Max file size to process")


# ---------------------------------------------------------------------------
# Queue — async job processing
# ---------------------------------------------------------------------------

class QueueConfig(BaseModel):
    """ARQ async queue configuration."""
    enabled: bool = Field(False)
    max_retries: int = Field(3, ge=0)
    retry_delay_seconds: int = Field(5, ge=1)
    job_timeout_seconds: int = Field(120, ge=10)


# ---------------------------------------------------------------------------
# LLM — Bifrost gateway
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    """LLM gateway configuration."""
    gateway_url: str = Field("http://bifrost:8080/v1", description="Bifrost endpoint")
    api_key: str = Field("dummy", description="API key (Bifrost uses dummy)")
    timeout_seconds: int = Field(60, ge=10)
    fallback_timeout_seconds: int = Field(120, ge=10, description="Timeout for fallback model")


# ---------------------------------------------------------------------------
# API — FastAPI server
# ---------------------------------------------------------------------------

class APIConfig(BaseModel):
    """FastAPI server configuration."""
    host: str = Field("0.0.0.0")
    port: int = Field(8000, ge=1, le=65535)
    webhook_path: str = Field("/webhook/zapi")
    human_webhook_path: str = Field("/webhook/humano")
    health_path: str = Field("/health")


# ---------------------------------------------------------------------------
# Tools — domain tool registration
# ---------------------------------------------------------------------------

class ToolRef(BaseModel):
    """Reference to a domain tool in the client's tools/ folder."""
    module: str = Field(..., description="Python module name (e.g. 'echo_tools')")
    functions: list[str] = Field(
        default_factory=list,
        description="Function names to import. Empty = import all @function_tool decorated."
    )


# ---------------------------------------------------------------------------
# Data — business data files
# ---------------------------------------------------------------------------

class DataFileRef(BaseModel):
    """Reference to a business data file."""
    key: str = Field(..., description="Key to access in runtime (e.g. 'catalog', 'shipping')")
    file: str = Field(..., description="Path relative to client data/ dir (e.g. 'cardapio.json')")
    format: str = Field("json", description="File format: 'json', 'csv', 'yaml'")


# ---------------------------------------------------------------------------
# Root config — the full contract
# ---------------------------------------------------------------------------

class FrameworkConfig(BaseModel):
    """
    Root configuration model.
    
    This is the complete contract between a client's config.yaml
    and the framework core. Every section maps 1:1 to a core module.
    
    Required sections: agent
    Everything else has sensible defaults and can be omitted.
    """
    # Identity (required)
    client_id: str = Field(..., description="Unique client identifier (e.g. 'lecrocant', 'example')")
    agent: AgentConfig

    # Core modules (all optional with defaults)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    human: HumanConfig = Field(default_factory=HumanConfig)
    habits: HabitsConfig = Field(default_factory=HabitsConfig)
    messaging: MessagingConfig = Field(default_factory=MessagingConfig)
    follow_up: FollowUpConfig = Field(default_factory=FollowUpConfig)
    media: MediaConfig = Field(default_factory=MediaConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    llm: LLMConfig = Field(default_factory=LLMConfig)
    api: APIConfig = Field(default_factory=APIConfig)

    # Client-specific resources
    tools: list[ToolRef] = Field(default_factory=list)
    data_files: list[DataFileRef] = Field(default_factory=list)

    # Metadata
    version: str = Field("1.0.0", description="Config schema version for migrations")

    @property
    def client_dir(self) -> Path:
        """Resolve path to this client's directory."""
        return Path("clients") / self.client_id
