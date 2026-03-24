"""
Zuper Agent Framework — Config Schema
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


# ---------------------------------------------------------------------------
# SDK 0.12 Native Features
# ---------------------------------------------------------------------------

class SDKSessionsConfig(BaseModel):
    """SDK 0.12 native Sessions with Redis.
    Replaces manual history management from session.py."""
    enabled: bool = Field(True, description="Use SDK native Redis sessions for conversation history")
    redis_key_prefix: str = Field("zuper:session:", description="Redis key prefix for SDK sessions")
    history_limit: int = Field(50, ge=5, description="Max messages kept in SDK session history")
    ttl: int = Field(10800, ge=60, description="Session TTL in seconds (default 3h)")


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

class HabitsConfig(BaseModel):
    """Operational habits (hybrid search RRF + dedup).
    DEFAULT OFF — enable when agent should learn from human resolutions."""
    enabled: bool = Field(False)
    embedding_model: str = Field("openai/text-embedding-3-small", description="Embedding model via Bifrost")
    embedding_dimensions: int = Field(1536)
    dedup_threshold: float = Field(0.020, ge=0.0, le=1.0, description="RRF dedup threshold (production-validated)")
    rrf_k: int = Field(60, ge=1, description="RRF ranking constant")
    match_count: int = Field(3, ge=1, description="Max results from hybrid search")
    search_before_escalate: bool = Field(True, description="Check habits before escalating to human")
    table_name: str = Field("operational_habits")


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
    """Media processing.
    DEFAULT OFF — enable per client when they need audio/image/PDF."""
    enabled: bool = Field(False)
    supported_types: list[MediaType] = Field(default_factory=list)
    audio_model: str = Field("whisper-1", description="Whisper model for transcription")
    image_model: str = Field("openai/gpt-4o-mini", description="Vision model via Bifrost")
    max_file_size_mb: int = Field(25, ge=1, description="Max file size to process")


# ---------------------------------------------------------------------------
# Queue — async job processing
# DEFAULT OFF — only for high-availability setups
# ---------------------------------------------------------------------------

class QueueConfig(BaseModel):
    """ARQ async queue.
    DEFAULT OFF — enable when messages can't be lost on restart."""
    enabled: bool = Field(False)
    max_retries: int = Field(3, ge=0)
    retry_delay_seconds: int = Field(5, ge=1)
    job_timeout_seconds: int = Field(120, ge=10)


# ---------------------------------------------------------------------------
# LLM — Bifrost gateway
# ALWAYS ON — every agent needs LLM access
# ---------------------------------------------------------------------------

class LLMConfig(BaseModel):
    """LLM gateway via Bifrost.
    ALWAYS ON — handles model routing and automatic fallback."""
    gateway_url: str = Field("http://bifrost:8080/v1", description="Bifrost endpoint")
    api_key: str = Field("dummy", description="API key (Bifrost uses dummy)")
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
# Data — business data files
# ---------------------------------------------------------------------------

class DataFileRef(BaseModel):
    """Reference to a business data file in the client's data/ folder."""
    key: str = Field(..., description="Key to access at runtime (e.g. 'catalog', 'shipping')")
    file: str = Field(..., description="Path relative to client data/ dir (e.g. 'cardapio.json')")
    format: str = Field("json", description="File format: 'json', 'csv', 'yaml'")


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

    # Metadata
    version: str = Field("1.0.0", description="Config schema version for future migrations")

    @property
    def client_dir(self) -> Path:
        """Resolve path to this client's directory."""
        return Path("clients") / self.client_id
