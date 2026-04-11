"""
Aleph Framework — Config Schema
======================================
Pydantic models defining the exact shape of config.yaml.

Defaults are extracted from Laura (Le Crocant) production values
and validated across 81 test scenarios.

Default tiers:
  ALWAYS ON  — no config needed, framework handles it
  DEFAULT ON — enabled by default, client can disable
  DEFAULT OFF — disabled by default, client opts in

SDK 0.12.5 native features mapped:
  - Sessions Redis (openai-agents[redis])
  - Guardrails (input/output/tool tripwire system)
  - Handoffs (peer transfer + manager/as_tool patterns)
  - Human-in-the-loop (interruption + approval flow)
  - Tracing (built-in run/tool/decision tracking)
  - MCP tools (built-in server integration)

Debug/Observability:
  - SDK tracing (always on by default — every run auditable)
  - Structured logging (JSON, configurable level)
  - Request ID tracking per message
  - Guardrail decision logging
  - Tool call logging with latency
"""

from __future__ import annotations

import os
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class GuardrailAction(str, Enum):
    """What to do when an input guardrail pattern matches."""
    ESCALATE = "escalate"                    # escalonar (consulta hábito primeiro se habilitado)
    ESCALATE_NO_HABIT = "escalate_no_habit"  # sempre escalonar, nunca consultar hábito
    TAKEOVER = "takeover"                    # transbordo direto (humano assume)
    REDIRECT = "redirect"                    # redireciona com mensagem custom
    TOOL_REQUIRED = "tool_required"          # força tool_choice = required
    INJECT = "inject"                        # injeta instrução extra no input do LLM
    BLOCK = "block"                          # bloqueia e responde com frase segura
    BYPASS_LLM = "bypass_llm"               # cálculo mecânico, pula o LLM
    CONTINUE = "continue"                    # segue pro LLM normalmente


class OutputGuardrailType(str, Enum):
    """Types of output guardrail checks."""
    FABRICATION = "fabrication"              # endereço, dados internos, unidades inventadas
    PRICE_LEAK = "price_leak"               # preço solto fora de contexto de orçamento
    GHOST_ESCALATION = "ghost_escalation"   # LLM diz que vai escalonar mas não chamou tool
    CUSTOM_REGEX = "custom_regex"           # pattern customizado pelo cliente


class MediaType(str, Enum):
    """Supported media types for processing."""
    AUDIO = "audio"
    IMAGE = "image"
    PDF = "pdf"


class LogLevel(str, Enum):
    """Logging levels."""
    DEBUG = "DEBUG"
    INFO = "INFO"
    WARNING = "WARNING"
    ERROR = "ERROR"


class HandoffMode(str, Enum):
    """SDK 0.12 handoff patterns."""
    PEER = "peer"        # handoff: agent transfers control to another agent
    MANAGER = "manager"  # as_tool: central agent invokes sub-agents as tools


class OnInterruptAction(str, Enum):
    """What to do when an off-topic message is detected mid-flow."""
    HOLD = "hold"    # re-ask current step, ignore off-topic message, skip LLM
    PAUSE = "pause"  # LLM answers the off-topic message, then re-asks the step


class OnCompleteAction(str, Enum):
    """What to do when all flow steps are answered."""
    CONTINUE_TO_LLM = "continue_to_llm"  # inject collected data, pass to LLM
    SEND_MESSAGE    = "send_message"      # send fixed message, done
    WEBHOOK         = "webhook"           # POST collected data to external URL
    ESCALATE        = "escalate"          # escalate to human with collected context
    START_FLOW      = "start_flow"        # chain into another flow


# ---------------------------------------------------------------------------
# Debug & Observability — ALWAYS ON by default, fully auditable
# ---------------------------------------------------------------------------

class TracingConfig(BaseModel):
    """SDK 0.12 built-in tracing configuration.
    Always enabled by default — every agent run is auditable."""
    enabled: bool = Field(True, description="Enable SDK tracing (default: always on)")
    export_to: str = Field("console", description="Trace export: 'console', 'braintrust', 'opentelemetry', 'custom'")
    export_endpoint: str = Field("", description="Endpoint URL when export_to is 'opentelemetry' or 'custom'")
    include_tool_calls: bool = Field(True, description="Log individual tool calls with input/output")
    include_model_io: bool = Field(True, description="Log model input/output (disable for PII-sensitive clients)")
    sample_rate: float = Field(1.0, ge=0.0, le=1.0, description="Trace sampling rate (1.0 = trace everything)")


class LoggingConfig(BaseModel):
    """Structured logging for framework internals.
    JSON by default — every event is parseable and searchable."""
    level: LogLevel = Field(LogLevel.INFO, description="Minimum log level")
    format: str = Field("json", description="Log format: 'json' (structured) or 'text' (human-readable)")
    include_request_id: bool = Field(True, description="Attach unique request ID per message through entire pipeline")
    log_guardrail_decisions: bool = Field(True, description="Log every guardrail match/skip with pattern name and action")
    log_tool_calls: bool = Field(True, description="Log tool calls with name, latency ms, success/failure")
    log_llm_latency: bool = Field(True, description="Log LLM call duration per request")
    log_session_events: bool = Field(True, description="Log session create/load/save/expire events")
    log_human_events: bool = Field(True, description="Log escalation, takeover, release, habit-learn events")
    log_buffer_events: bool = Field(True, description="Log message buffer consolidation events")
    log_to_file: str = Field("", description="Optional file path for log output (empty = stdout only)")


class DebugConfig(BaseModel):
    """Debug & observability — combined tracing + logging.
    Default: everything auditable out of the box.
    Tune down individual flags for production if needed."""
    tracing: TracingConfig = Field(default_factory=TracingConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)
    verbose_errors: bool = Field(True, description="Include full stack traces in error responses (dev mode)")
    dry_run: bool = Field(False, description="Process entire pipeline but don't send messages (testing)")


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
    parallel_tool_calls: bool = Field(True, description="Allow LLM to call multiple tools in parallel (DEFAULT ON)")


# ---------------------------------------------------------------------------
# SDK 0.12 Native Features
# ---------------------------------------------------------------------------

class SDKSessionsConfig(BaseModel):
    """SDK 0.12 native Sessions with Redis.
    Replaces manual history management from session.py."""
    enabled: bool = Field(True, description="Use SDK native Redis sessions for conversation history")
    redis_key_prefix: str = Field("aleph:session:", description="Redis key prefix for SDK sessions")
    history_limit: int = Field(50, ge=5, description="Max messages kept in SDK session history")
    ttl: int = Field(10800, ge=60, description="Session TTL in seconds (default 3h)")
    # Episodic memory — Phase 12
    max_raw_turns: int = Field(8, ge=2, le=50, description="Rolling raw turn window. Compression fires when full.")
    compression_model: str = Field("", description="Model for compression. Empty = fallback_model → agent.model")
    gap_compression_hours: float = Field(3.0, ge=0.5, description="Hours of inactivity that trigger deep compression")
    summary_ttl_days: int = Field(30, ge=1, description="Days to retain episodic summary before expiry")


class SDKGuardrailsConfig(BaseModel):
    """SDK 0.12 native guardrails (complementary to our deterministic ones).
    Our guardrails: run BEFORE LLM — deterministic, zero cost, regex-based.
    SDK guardrails: run IN PARALLEL with LLM — tripwire system, can use LLM.
    Both layers active by default = defense in depth."""
    enabled: bool = Field(True, description="Enable SDK native guardrails layer")
    run_input_guardrails: bool = Field(True, description="Run SDK input guardrails on first agent")
    run_output_guardrails: bool = Field(True, description="Run SDK output guardrails on final agent")
    run_tool_guardrails: bool = Field(True, description="Run SDK tool guardrails on function_tool calls")


class SDKHandoffsConfig(BaseModel):
    """SDK 0.12 native handoffs for multi-agent orchestration."""
    enabled: bool = Field(False, description="Enable multi-agent handoffs (most agents start single-agent)")
    mode: HandoffMode = Field(HandoffMode.PEER, description="Handoff pattern: peer transfer or manager/as_tool")
    nest_history: bool = Field(True, description="Collapse prior transcript into single message for downstream agent")
    max_turns: int = Field(15, ge=1, description="Max turns before raising MaxTurnsExceeded")


class SDKConfig(BaseModel):
    """Aggregated SDK 0.12 native feature configuration."""
    sessions: SDKSessionsConfig = Field(default_factory=SDKSessionsConfig)
    guardrails: SDKGuardrailsConfig = Field(default_factory=SDKGuardrailsConfig)
    handoffs: SDKHandoffsConfig = Field(default_factory=SDKHandoffsConfig)


# ---------------------------------------------------------------------------
# Guardrails — our deterministic layer (BEFORE LLM)
# ---------------------------------------------------------------------------

class InputPattern(BaseModel):
    """A single input guardrail pattern rule.
    Evaluated BEFORE the LLM call — deterministic, no cost."""
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
    """A single output guardrail rule.
    Evaluated AFTER the LLM response — deterministic regex checks."""
    name: str
    type: OutputGuardrailType
    patterns: list[str] = Field(default_factory=list, description="Regex patterns to detect violations")
    exempt_intents: list[str] = Field(default_factory=list, description="Input intents that skip this rule")
    safe_response: str = Field(
        "Desculpe, não tenho essa informação. Posso verificar com a equipe?",
        description="Replacement message when output is blocked"
    )
    enabled: bool = True


class GuardrailsConfig(BaseModel):
    """Our deterministic guardrail layer (regex/keywords).
    Runs BEFORE LLM (input) and AFTER LLM (output).
    Complementary to SDK native guardrails which run IN PARALLEL."""
    input_patterns: list[InputPattern] = Field(default_factory=list)
    output_rules: list[OutputGuardrailRule] = Field(default_factory=list)
    # ALWAYS ON — text normalization for pattern matching
    normalize_accents: bool = Field(True, description="Remove accents before matching")
    normalize_lowercase: bool = Field(True, description="Lowercase before matching")
    # DEFAULT ON — generic output guards (work for any agent)
    enable_fabrication_guard: bool = Field(True, description="Block fabricated addresses, internal data, fake branches")
    enable_price_leak_guard: bool = Field(True, description="Block loose prices outside budget context")
    enable_ghost_escalation_guard: bool = Field(True, description="Detect LLM claiming escalation without calling tool")


# ---------------------------------------------------------------------------
# Session — our Redis layer (buffer, anti-spam, locks)
# ALWAYS ON — every WhatsApp agent needs these
# ---------------------------------------------------------------------------

class SessionConfig(BaseModel):
    """Our Redis session layer for WhatsApp-specific state.
    ALWAYS ON — solves WhatsApp platform quirks that the SDK doesn't handle.
    SDK Sessions handle conversation history separately."""
    # ALWAYS ON — message consolidation (WhatsApp sends messages in chunks)
    buffer_timeout: int = Field(8, ge=1, le=60, description="Seconds to wait for chunked messages")
    # ALWAYS ON — duplicate prevention (Z-API redelivers on timeout)
    antispam_ttl: int = Field(120, ge=10, description="TTL for messageId dedup")
    # ALWAYS ON — race condition prevention
    processing_lock_ttl: int = Field(30, ge=5, description="Lock per phone to prevent duplicate responses")
    # Context merge (name, neighborhood, preferences)
    context_ttl: int = Field(10800, ge=60, description="Client context TTL (default 3h)")


# ---------------------------------------------------------------------------
# Human-in-the-loop — escalation + takeover
# DEFAULT ON — most agents need human backup
# ---------------------------------------------------------------------------

class HumanConfig(BaseModel):
    """Human-in-the-loop and takeover.
    DEFAULT ON — most B2B agents need human fallback.
    Goes beyond SDK HITL: handles WhatsApp quote/reply flow,
    transbordo (human takes over entire chat), and RELEASE."""
    enabled: bool = Field(True)
    # Escalation (agent pauses, human responds via WhatsApp quote)
    escalation_session_ttl: int = Field(7200, ge=60, description="Paused session TTL (default 2h)")
    # Takeover (human assumes full control of chat)
    takeover_lock_ttl: int = Field(3600, ge=60, description="Takeover lock duration (default 1h)")
    takeover_renew_on_message: bool = Field(True, description="Renew lock on each human message")
    release_keyword: str = Field("RELEASE", description="Keyword to release takeover back to agent")
    # Notification
    notify_via: str = Field("zapi", description="Notification channel: 'zapi' or 'n8n'")
    responsible_phones: list[str] = Field(default_factory=list, description="Phone numbers to notify on escalation")
    # LID mapping (WhatsApp internal ID → real phone number)
    lid_mapping_ttl: int = Field(86400, ge=3600, description="LID↔phone cache TTL (default 24h)")


# ---------------------------------------------------------------------------
# Habits — operational learning
# DEFAULT OFF — not every agent needs to learn
# ---------------------------------------------------------------------------

"""
SCHEMA DIFF — Campos novos para HabitsConfig em core/registry/schema.py
=========================================================================
Substitua a classe HabitsConfig existente por esta versão.
Campos adicionados: auto_migrate, schema
"""


class HabitsConfig(BaseModel):
    """Operational habits (hybrid search RRF + dedup).
    DEFAULT OFF — enable when agent should learn from human resolutions.

    Database: any Postgres with pgvector extension.
      - Self-hosted: just set DATABASE_URL
      - Supabase: set DATABASE_URL (pooler) + DATABASE_MIGRATION_URL (direct)

    Auto-migrate: when enabled, creates extensions, table, indexes,
    and the hybrid search function on first startup.
    """
    enabled: bool = Field(False)
    auto_migrate: bool = Field(True, description="Create table/functions on startup if not exist")
    embedding_model: str = Field("openai/text-embedding-3-small", description="Embedding model via Bifrost")
    embedding_dimensions: int = Field(1536)
    dedup_threshold: float = Field(0.020, ge=0.0, le=1.0, description="Cosine distance dedup threshold (production-validated)")
    rrf_k: int = Field(60, ge=1, description="RRF ranking constant")
    match_count: int = Field(3, ge=1, description="Max results from hybrid search")
    search_before_escalate: bool = Field(True, description="Check habits before escalating to human")
    table_name: str = Field("operational_habits", description="Postgres table name")
    schema: str = Field("public", description="Postgres schema (useful for Supabase)")

class KnowledgeConfig(BaseModel):
    """Knowledge base RAG (hybrid search + contextual retrieval).
    DEFAULT OFF — enable when agent needs a searchable knowledge base.

    Database: any Postgres with pgvector extension.
    Can share the same DATABASE_URL as habits (different schema/table).

    Auto-migrate: when enabled, creates schema, table, indexes,
    and the hybrid search function on first startup.
    """
    enabled: bool = Field(False)
    auto_migrate: bool = Field(True, description="Create table/functions on startup if not exist")
    embedding_model: str = Field("openai/text-embedding-3-small", description="Embedding model")
    embedding_dimensions: int = Field(1536, description="Must match the embedding model output dims")
    schema: str = Field("knowledge", description="Postgres schema (separate from habits)")
    table_name: str = Field("knowledge_base", description="Postgres table name")
    auto_search: bool = Field(True, description="Search knowledge before every LLM call")
    auto_search_top_k: int = Field(5, ge=1, le=20, description="How many chunks to inject pre-LLM")
    tool_search: bool = Field(True, description="Also expose as a callable tool for the agent")
    similarity_threshold: float = Field(0.7, ge=0.0, le=1.0, description="Min relevance to include")
    match_count: int = Field(5, ge=1, description="Max results from hybrid search")
    rrf_k: int = Field(60, ge=1, description="RRF ranking constant")
    chunk_size: int = Field(500, ge=100, le=2000, description="Tokens per chunk during ingestion")
    chunk_overlap: int = Field(75, ge=0, le=500, description="Token overlap between chunks")
    rerank: bool = Field(False, description="Enable reranking (future)")

# ---------------------------------------------------------------------------
# Messaging — WhatsApp I/O
# ALWAYS ON — core functionality
# ---------------------------------------------------------------------------

class DisclaimerConfig(BaseModel):
    """Disclaimer appended to agent messages. DEFAULT ON."""
    enabled: bool = True
    text: str = "Agente de experimentação Zuper"
    separator: str = "\n\n---\n"


class MessagingConfig(BaseModel):
    """Z-API messaging configuration.
    ALWAYS ON — filters and humanized sending are non-negotiable."""
    provider: str = Field("zapi", description="Messaging provider")
    # ALWAYS ON — humanized sending
    send_as_paragraphs: bool = Field(True, description="Split response by paragraph into multiple messages")
    delay_min_ms: int = Field(800, ge=0, description="Min delay between messages (human-like)")
    delay_max_ms: int = Field(2500, ge=0, description="Max delay between messages (human-like)")
    # DEFAULT ON — disclaimer
    disclaimer: DisclaimerConfig = Field(default_factory=DisclaimerConfig)
    # ALWAYS ON — Z-API webhook filters
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
# DEFAULT OFF — depends on use case
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# Flows — declarative state machine
# DEFAULT OFF — enable when agent needs structured multi-step processes
# ---------------------------------------------------------------------------

class OnCompleteConfig(BaseModel):
    """What happens when a flow finishes all its steps."""
    action: OnCompleteAction = Field(OnCompleteAction.SEND_MESSAGE, description="Action to take on flow completion")
    message: str = Field("", description="Static message to send (send_message / webhook then)")
    url: str = Field("", description="Webhook URL (action=webhook)")
    method: str = Field("POST", description="HTTP method for webhook call")
    then: str = Field("", description="Action after webhook returns: 'send_message' (sends message field)")
    flow_id: str = Field("", description="Flow to chain into (action=start_flow)")
    inject_summary: bool = Field(
        True, description="Inject collected data summary into LLM context (action=continue_to_llm)"
    )


class StepConfig(BaseModel):
    """A single step in a flow. The framework sends message, collects the reply, advances."""
    id: str = Field(..., description="Unique step identifier within the flow")
    message: str = Field(..., description="Message sent to the user at this step")
    collect_as: str = Field("", description="Key under which the user's reply is stored in collected data")
    next: str = Field("", description="ID of the next step. Empty = last step, triggers on_complete")
    on_complete: OnCompleteConfig = Field(
        default_factory=OnCompleteConfig,
        description="What to do when this is the last step and the user has answered",
    )


class FlowDefinition(BaseModel):
    """A complete multi-step flow definition."""
    id: str = Field(..., description="Unique flow identifier (e.g. 'onboarding', 'checkout')")
    trigger_keywords: list[str] = Field(default_factory=list, description="Keywords that start this flow")
    trigger_regex: list[str] = Field(default_factory=list, description="Regex patterns that start this flow")
    steps: list[StepConfig] = Field(default_factory=list, description="Ordered list of steps")
    on_interrupt: OnInterruptAction = Field(
        OnInterruptAction.PAUSE,
        description="Behavior when an off-topic message is detected mid-flow",
    )
    state_ttl: int = Field(
        0, ge=0,
        description="Flow state TTL in seconds. 0 = use FlowsConfig.default_state_ttl",
    )

    @field_validator("trigger_regex", mode="before")
    @classmethod
    def validate_trigger_regex(cls, v: list[str]) -> list[str]:
        import re
        for pattern in v:
            try:
                re.compile(pattern)
            except re.error as e:
                raise ValueError(f"Invalid trigger regex '{pattern}': {e}")
        return v


class FlowsConfig(BaseModel):
    """Declarative multi-step conversation flows (state machine).
    DEFAULT OFF — enable when the agent needs to guide users through structured processes.

    State is stored in Redis at aleph:{client_id}:flow:{phone}.
    Each flow is triggered by keywords/regex and advances step-by-step,
    collecting user replies. On completion, configurable on_complete action runs.

    Off-topic detection: a message that matches ANOTHER flow's trigger keywords/regex.
    on_interrupt controls what happens: hold (re-ask) or pause (LLM answers, then re-ask).
    """
    enabled: bool = Field(False)
    default_state_ttl: int = Field(1800, ge=60, description="Default flow state TTL in seconds (30min)")
    flows: list[FlowDefinition] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Follow-up — proactive re-engagement
# DEFAULT OFF — depends on use case
# ---------------------------------------------------------------------------

class FollowUpStep(BaseModel):
    """One step in the follow-up sequence."""
    delay_minutes: int = Field(..., ge=1)
    message: str = Field(..., description="Message template (supports {name} placeholder)")


class FollowUpConfig(BaseModel):
    """Proactive follow-up after inactivity.
    DEFAULT OFF — enable and configure steps per client."""
    enabled: bool = Field(False)
    steps: list[FollowUpStep] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Media — audio, image, PDF processing
# DEFAULT OFF — adds cost (Whisper API, Vision API)
# ---------------------------------------------------------------------------

class MediaConfig(BaseModel):
    """Media processing — audio transcription, image description, PDF extraction.
    DEFAULT OFF — adds API cost (Whisper, Vision). Enable per client."""
    enabled: bool = Field(False, description="DEFAULT OFF — enable audio/image/PDF processing")
    supported_types: list[MediaType] = Field(
        default_factory=list,
        description="Which media types to process: audio, image, pdf",
    )
    audio_model: str = Field("whisper-1", description="OpenAI Whisper model for audio transcription")
    image_model: str = Field("gpt-4o-mini", description="Vision model for image description")
    image_prompt: str = Field(
        "Descreva o conteúdo desta imagem de forma concisa e objetiva.",
        description="Prompt sent with image to vision model",
    )
    max_file_size_mb: int = Field(25, ge=1, le=100, description="Max media file size to process in MB")
    pdf_max_pages: int = Field(10, ge=1, le=100, description="Max PDF pages to extract text from")


# ---------------------------------------------------------------------------
# Queue — async job processing
# DEFAULT OFF — only for high-availability setups
# ---------------------------------------------------------------------------

class QueueJobConfig(BaseModel):
    """A single background job triggered by a pipeline event.

    Triggers:
      pipeline_complete   — fires after every successful agent response
      flow_complete       — fires when a specific flow reaches on_complete
      escalation_start    — fires when human escalation begins

    Actions:
      webhook             — POST job payload to external URL (CRM, Sheets, Slack, etc.)
    """
    trigger: str = Field(..., description="pipeline_complete | flow_complete | escalation_start")
    flow_id: str = Field("", description="Required when trigger=flow_complete — which flow")
    action: str = Field("webhook", description="Job action type: 'webhook'")
    webhook_url: str = Field("", description="URL to POST the job payload to")
    include_fields: list[str] = Field(
        default_factory=list,
        description="Fields from pipeline result to include: phone, response, elapsed_seconds, collected"
    )
    timeout_seconds: int = Field(10, ge=1, description="HTTP timeout for this job")


class QueueConfig(BaseModel):
    """Background job queue — fire-and-forget tasks after pipeline completion.
    DEFAULT OFF — enable when agent needs to update external systems after every message.

    Jobs run asynchronously after the response is sent. Errors are logged, never raised.
    Redis-backed: aleph:{client_id}:queue (LPUSH/BRPOP).
    """
    enabled: bool = Field(False)
    jobs: list[QueueJobConfig] = Field(default_factory=list)
    max_retries: int = Field(3, ge=0, description="Retry count on job failure")
    retry_delay_seconds: int = Field(5, ge=1, description="Delay between retries")
    job_timeout_seconds: int = Field(120, ge=10, description="Max job execution time")


# ---------------------------------------------------------------------------
# LLM — Bifrost gateway
# ALWAYS ON — every agent needs LLM access
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    """LLM connection configuration.
    ALWAYS ON — every agent needs LLM access.

    Two modes:
      1. Bifrost (default) — local gateway that routes to multiple providers
      2. Direct API key — connects straight to the provider
    """
    provider: str = Field(
        "",
        description=(
            "LLM provider: 'bifrost' (default), 'openai', 'gemini', 'deepseek', "
            "'openrouter', 'custom'. Empty = auto-detect from env vars."
        ),
    )
    gateway_url: str = Field("http://bifrost:8080/v1", description="Bifrost endpoint (only used when provider=bifrost)")
    api_key: str = Field("dummy", description="API key (Bifrost uses dummy, direct uses real key from .env)")
    timeout_seconds: int = Field(60, ge=10, description="Primary model timeout")
    fallback_timeout_seconds: int = Field(120, ge=10, description="Fallback model timeout (DeepSeek needs 120s)")


# ---------------------------------------------------------------------------
# API — FastAPI server
# ALWAYS ON — the entry point
# ---------------------------------------------------------------------------

class APIConfig(BaseModel):
    """FastAPI server configuration."""
    host: str = Field("0.0.0.0")
    port: int = Field(8000, ge=1, le=65535)
    webhook_path: str = Field("/webhook/zapi")
    human_webhook_path: str = Field("/webhook/humano")
    health_path: str = Field("/health")


# ---------------------------------------------------------------------------
# Tools — domain tool registration (webhook + code)
# ---------------------------------------------------------------------------

class ToolType(str, Enum):
    """Tool implementation type."""
    WEBHOOK = "webhook"  # N8N / external HTTP — zero code, YAML only
    CODE = "code"        # Python module in client's tools/ folder


class ParamsIn(str, Enum):
    """Where webhook parameters are sent."""
    BODY = "body"    # JSON body (default for POST — N8N pattern)
    QUERY = "query"  # URL query string (default for GET)
    URL = "url"      # Resolve {param} placeholders in URL (REST API pattern)
    AUTO = "auto"    # Auto-detect: if URL has {param} → url, else POST=body / GET=query


class WebhookParam(BaseModel):
    """A single parameter for a webhook tool."""
    type: str = Field("string", description="Parameter type: 'string', 'number', 'boolean', 'integer'")
    description: str = Field("", description="Description shown to the LLM")
    required: bool = Field(True, description="Whether the LLM must provide this")
    default: Any = Field(None, description="Default value (if set, injected automatically)")
    hidden: bool = Field(False, description="If true, parameter is hidden from LLM and injected with default value")
    enum: list[str] = Field(default_factory=list, description="Allowed values (optional)")


class ToolRef(BaseModel):
    """Tool definition — supports both webhook (N8N) and code (Python) tools.

    Webhook mode (type='webhook'):
      Declares a tool entirely in YAML. The framework generates a @function_tool
      that POSTs to the webhook URL and returns the JSON response.
      The junior never writes Python.

      Three URL patterns supported:
        1. POST body (N8N):     webhook_url: "http://n8n:5678/webhook/xxx"
                                params_in: "body"  (default for POST)
        2. GET query:           webhook_url: "http://api.com/search"
                                params_in: "query"  (default for GET)
        3. URL template (REST): webhook_url: "https://viacep.com.br/ws/{cep}/json/"
                                params_in: "url"
        4. Auto-detect:         params_in: "auto" (default)
                                → if URL has {param} placeholders → resolves them
                                → remaining params → body (POST) or query (GET)

    Code mode (type='code'):
      References a Python module in the client's tools/ folder.
      Functions decorated with @function_tool are auto-discovered.
    """
    name: str = Field(..., description="Tool name (what the LLM sees)")
    type: ToolType = Field(ToolType.CODE, description="'webhook' for N8N/HTTP, 'code' for Python")
    description: str = Field("", description="Tool description (what the LLM sees)")

    # --- Webhook mode fields ---
    webhook_url: str = Field("", description="Full URL (supports {param} placeholders)")
    method: str = Field("POST", description="HTTP method: POST or GET")
    params_in: ParamsIn = Field(
        ParamsIn.AUTO,
        description="Where to send params: 'body' (POST JSON), 'query' (?key=val), 'url' ({param} in URL), 'auto' (detect)"
    )
    parameters: dict[str, WebhookParam] = Field(
        default_factory=dict,
        description="Parameters: key = param name, value = WebhookParam config"
    )
    returns: str = Field("", description="Description of what the tool returns (for LLM context)")
    timeout_seconds: int = Field(30, ge=1, description="HTTP timeout for webhook call")

    # --- Code mode fields ---
    module: str = Field("", description="Python module name (e.g. 'echo_tools') — code mode only")
    functions: list[str] = Field(
        default_factory=list,
        description="Function names to import. Empty = auto-discover @function_tool — code mode only"
    )


# ---------------------------------------------------------------------------
# Sub-Agents — specialist agents invoked as tools (MANAGER pattern)
# DEFAULT OFF — most agents start single-agent
# ---------------------------------------------------------------------------

class SubAgentConfig(BaseModel):
    """A specialist sub-agent the main orchestrator can invoke as a tool.

    The sub-agent runs its own full Agent+Runner loop and returns a result string.
    The orchestrator LLM decides when to call it — and can call multiple in parallel
    when agent.parallel_tool_calls=true.

    Two definition modes:
      inline: instructions + tools defined directly here
      ref:    points to another agent directory (e.g. 'clients/financeiro')
    """
    name: str = Field(..., description="Internal name for the sub-agent")
    tool_name: str = Field(..., description="Tool name exposed to the orchestrator LLM")
    tool_description: str = Field(..., description="Description shown to the orchestrator LLM")
    instructions: str = Field("", description="Sub-agent system prompt (inline mode)")
    ref: str = Field("", description="Path to another agent dir — loads its config (ref mode)")
    model: str = Field("", description="Model override. Empty = inherits main agent model")
    tools: list[ToolRef] = Field(default_factory=list, description="Tools available to this sub-agent")
    max_turns: int = Field(5, ge=1, description="Max agent turns for this sub-agent invocation")


# ---------------------------------------------------------------------------
# Data — business data files
# ---------------------------------------------------------------------------

class DataFileRef(BaseModel):
    """Reference to a business data file in the client's data/ folder."""
    key: str = Field(..., description="Key to access at runtime (e.g. 'catalog', 'shipping')")
    file: str = Field(..., description="Path relative to client data/ dir (e.g. 'cardapio.json')")
    format: str = Field("json", description="File format: 'json', 'csv', 'yaml'")


# ---------------------------------------------------------------------------
# Self-Awareness — prior state context injection (DEFAULT OFF)
# ---------------------------------------------------------------------------

class SelfAwarenessConfig(BaseModel):
    """Agent self-awareness — inject prior state context before LLM run.
    DEFAULT OFF. Reads episodic summary + flow/escalation state from Redis.
    Only injects when relevance gates pass (gap + age checks)."""
    enabled: bool = Field(False, description="DEFAULT OFF — inject prior state context")
    return_gap_minutes: float = Field(30.0, ge=1.0, description="Min inactivity gap (minutes) before injection fires")
    max_injection_age_hours: float = Field(4.0, ge=0.5, description="States older than this are not injected")
    include_flow: bool = Field(True, description="Include interrupted flow state in injection")
    include_escalation: bool = Field(True, description="Include escalation state in injection")
    include_summary: bool = Field(True, description="Include episodic summary in injection")


# ---------------------------------------------------------------------------
# Root config — the full contract
# ---------------------------------------------------------------------------

class FrameworkConfig(BaseModel):
    """
    Root configuration model — the complete contract.

    Every section maps 1:1 to a core module.
    Required: client_id, agent
    Everything else has production-validated defaults.

    Default tiers summary:
      ALWAYS ON:  session, messaging (filters+humanized), llm, api,
                  debug (tracing+logging), text normalization
      DEFAULT ON: human, guardrails (fabrication/price/ghost),
                  sdk.sessions, sdk.guardrails, disclaimer
      DEFAULT OFF: habits, follow_up, media, queue,
                   sdk.handoffs, dry_run
    """
    # Knowledge reference for RAG — optional, enables knowledge base features
    knowledge: KnowledgeConfig = Field(default_factory=KnowledgeConfig)

    # Identity (required)
    client_id: str = Field(..., description="Unique client identifier (e.g. 'lecrocant', 'example')")
    agent: AgentConfig

    # SDK 0.12 native features
    sdk: SDKConfig = Field(default_factory=SDKConfig)

    # Debug & Observability (ALWAYS ON by default)
    debug: DebugConfig = Field(default_factory=DebugConfig)

    # Our deterministic guardrails (complementary to SDK guardrails)
    guardrails: GuardrailsConfig = Field(default_factory=GuardrailsConfig)

    # Session — WhatsApp-specific state (ALWAYS ON)
    session: SessionConfig = Field(default_factory=SessionConfig)

    # Human-in-the-loop (DEFAULT ON)
    human: HumanConfig = Field(default_factory=HumanConfig)

    # Habits — operational learning (DEFAULT OFF)
    habits: HabitsConfig = Field(default_factory=HabitsConfig)

    # Messaging — Z-API I/O (ALWAYS ON)
    messaging: MessagingConfig = Field(default_factory=MessagingConfig)

    # Follow-up proativo (DEFAULT OFF)
    follow_up: FollowUpConfig = Field(default_factory=FollowUpConfig)

    # Flows — declarative state machine (DEFAULT OFF)
    flows: FlowsConfig = Field(default_factory=FlowsConfig)

    # Media processing (DEFAULT OFF)
    media: MediaConfig = Field(default_factory=MediaConfig)

    # Async queue (DEFAULT OFF)
    queue: QueueConfig = Field(default_factory=QueueConfig)

    # LLM gateway (ALWAYS ON)
    llm: LLMConfig = Field(default_factory=LLMConfig)

    # API server (ALWAYS ON)
    api: APIConfig = Field(default_factory=APIConfig)

    # Client-specific resources
    tools: list[ToolRef] = Field(default_factory=list)
    data_files: list[DataFileRef] = Field(default_factory=list)
    subagents: list[SubAgentConfig] = Field(default_factory=list, description="Specialist sub-agents (DEFAULT OFF)")

    # Self-awareness — prior state injection (DEFAULT OFF)
    self_awareness: SelfAwarenessConfig = Field(default_factory=SelfAwarenessConfig)

    # Metadata
    version: str = Field("1.0.0", description="Config schema version for future migrations")

    @property
    def client_dir(self) -> Path:
        agent_dir = os.environ.get("AGENT_DIR")
        if agent_dir:
            return Path(agent_dir)
        return Path("clients") / self.client_id
