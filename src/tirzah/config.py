from __future__ import annotations

import os
from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class MongoConfig(BaseModel):
    uri: str = "mongodb://localhost:27017"
    # Kept during the rename transition so existing local corpora remain visible.
    database: str = "mnemosyne_dev"


class PathConfig(BaseModel):
    ingest: Path = Path("data/ingest")
    archive: Path = Path("data/archive")
    dead_letter: Path = Path("data/dead_letter")
    staging: Path = Path("data/staging")


class RuntimeConfig(BaseModel):
    model_adapter: str = "mock"
    answer_adapter: str = "ollama_cli"
    ingestion_adapter: str = "mock"
    embedding_adapter: str = "mock"
    allow_http_ingestion_adapters: bool = False
    embedding_model: str = "nomic-embed-text:latest"
    embedding_dimensions: int = 16
    profile_command: list[str] | str | None = Field(default_factory=list)
    profile_command_mode: str = "single"
    profile_backfill_recommended_batch_limit: int = 25
    profile_backfill_web_max_batches: int = 10
    memory_agent_adapter: str | None = None
    retrieval_mode: str = "direct"
    # Recursive Cairn planning wrapper immediately below the front end.
    recursive_planning_enabled: bool = True
    planning_adapter: str | None = None
    planning_model: str | None = None
    planning_max_revisions: int = Field(default=3, ge=1, le=20)
    planning_max_steps: int = Field(default=12, ge=1, le=30)
    # Opt-in transient web evidence. Search uses a SearxNG JSON endpoint; results
    # are never promoted into durable graph memory automatically.
    web_research_enabled: bool = False
    web_search_base_url: str = "http://localhost:8080"
    web_timeout_seconds: float = 12.0
    web_max_results: int = 5
    web_max_pages: int = 2
    web_max_content_bytes: int = 500_000
    web_max_content_chars: int = 8_000
    web_allow_private_search_endpoint: bool = False
    # Blend lexical + query-vector similarity in node search (ADR-020). On by
    # default as of the real-corpus validation; only takes effect with a real
    # (non-mock) embedding adapter and degrades safely to lexical otherwise, so
    # it is harmless under the default mock adapter.
    hybrid_search_enabled: bool = True
    ollama_model: str = "gemma3:1b"
    memory_agent_model: str | None = None
    ollama_format: str | None = None
    memory_agent_ollama_format: str | None = "json"
    ollama_think: bool | str | None = False
    ollama_hide_thinking: bool = True
    ollama_base_url: str = "http://localhost:11434"
    # Resolved from PATH by default (portable); the HTTP path (ollama_base_url) is
    # preferred. Override via config.yaml or the OLLAMA_EXECUTABLE env var for a
    # non-PATH install (e.g. a WSL-mounted ollama.exe).
    ollama_executable: Path = Path("ollama")
    ollama_timeout_seconds: int = 180
    # Context window for the HTTP adapter. Must be large enough to hold the
    # conversation history + retrieved context, or Ollama silently truncates the
    # start of the prompt (dropping history). 0 = leave Ollama's default.
    ollama_num_ctx: int = 8192
    # Semantic precision via Mahalath (the Tirzah->Mahalath seam, off by default).
    # When enabled, retrieval resolves key terms to MPL labels/senses from Mahalath's
    # ontology and conditions the answer on them. Fail-soft: an absent/unreachable
    # Mahalath simply yields no labels. See tirzah/semantic.py.
    mahalath_enabled: bool = False
    mahalath_mongo_uri: str = "mongodb://localhost:27017"
    mahalath_mongo_db: str = "mahalath_dev"
    mahalath_language: str = "en"
    # Strict by default: drop fuzzy (partial/text) matches and keep only confident
    # label/exact/alias hits. A loose match attaching a wrong sense is worse than no
    # sense for precision-grade work; flip to false only to accept approximate hints.
    mahalath_strict: bool = True
    hoglah_db_path: Path = Path("data/hoglah/jobs.sqlite3")
    hoglah_ollama_host: str = "http://localhost:11434"
    # Decoupled topology: Tirzah is a pure submitter into the shared queue and a
    # SEPARATE `hoglah run --real` daemon executes jobs. hoglah_use_real is kept
    # for back-compat but is no longer used by the submitter (the daemon owns
    # real execution and its --ollama-host).
    hoglah_use_real: bool = True
    hoglah_wait_timeout_seconds: int | None = None
    # Where the daemon writes terminal results (must match the daemon's
    # HOGLAH_OUTPUT_DIR); Tirzah polls here.
    hoglah_output_dir: Path = Path("data/hoglah/outbox")
    # Result delivery: "poll" the output folder, or "callback" (Tirzah runs a
    # tiny HTTP receiver and hands Hoglah its own URL per job; falls back to the
    # output folder if a push is missed).
    hoglah_delivery: str = "poll"
    hoglah_callback_host: str = "127.0.0.1"
    hoglah_callback_port: int = 0
    # Submission transport: "store" (default — write to the shared SQLite queue and
    # await by poll/callback) or a messaging broker ("kafka" | "rabbitmq" | "redis"),
    # which publishes a job-request message and awaits the result over the same
    # broker. The matching `hoglah {kafka,rabbitmq,redis}-bridge` worker must be
    # running on these topics/queues/streams.
    hoglah_transport: str = "store"
    hoglah_kafka_bootstrap_servers: str = "localhost:9092"
    hoglah_kafka_input_topic: str = "hoglah-jobs"
    hoglah_kafka_results_topic: str = "hoglah-results"
    hoglah_rabbitmq_url: str = "amqp://guest:guest@localhost:5672/"
    hoglah_rabbitmq_input_queue: str = "hoglah-jobs"
    hoglah_redis_url: str = "redis://localhost:6379/0"
    hoglah_redis_input_stream: str = "hoglah-jobs"
    hoglah_redis_results_stream: str = "hoglah-results"


class QueueConfig(BaseModel):
    max_attempts: int = 3


class RetrievalConfig(BaseModel):
    context_char_budget: int = 4000
    prompt_token_budget: int = 2000
    reserved_response_tokens: int = 500
    memory_agent_max_iterations: int = 4
    # Conversational memory: how many prior turns of the session to thread into
    # the prompt, and how much of each answer to keep.
    conversation_history_turns: int = 6
    conversation_history_answer_chars: int = 600
    # Phase 2: also surface semantically-relevant EARLIER turns (beyond the recent
    # window) by embedding similarity. Off by default — it embeds each turn, which
    # adds latency; enable for long conversations with a real embedder.
    conversation_semantic_recall: bool = False
    conversation_semantic_recall_k: int = 3
    # Phase 3: decompose turns into typed semantic chunks (topic/intent/domain/...).
    # Off by default — it runs an extra LLM call per turn (async + durable when on).
    conversation_chunking: bool = False
    # Deep retrieval mode (ADR-020) — bounds for the agent loop + Python pre-rank.
    deep_max_iterations: int = 4
    deep_max_candidates: int = 50
    deep_shortlist_size: int = 12
    deep_page_size: int = 5
    # Phase 4: recursive Context Sufficiency Score driving the deep loop.
    deep_sufficiency_scoring: bool = True
    deep_sufficiency_stop: float = 9.0  # stop when sufficiency reaches this
    deep_sufficiency_plateau_floor: float = 8.0  # stop at/above this if plateaued
    deep_plateau_passes: int = 3
    deep_plateau_epsilon: float = 0.2


class AppConfig(BaseModel):
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)


# Env-var → (section, key) overrides, applied on top of the YAML (and even with
# no config file). Keeps the shared OLLAMA_BASE_URL / MONGO settings in one place
# so the Noa runtime can configure every sibling from a single .env.
_ENV_OVERRIDES: dict[str, tuple[str, str]] = {
    "TIRZAH_MONGO_URI": ("mongo", "uri"),
    "TIRZAH_MONGO_DB": ("mongo", "database"),
    "OLLAMA_BASE_URL": ("runtime", "ollama_base_url"),
    "OLLAMA_EXECUTABLE": ("runtime", "ollama_executable"),
    "TIRZAH_WEB_RESEARCH_ENABLED": ("runtime", "web_research_enabled"),
    "TIRZAH_RECURSIVE_PLANNING_ENABLED": ("runtime", "recursive_planning_enabled"),
    "TIRZAH_WEB_SEARCH_BASE_URL": ("runtime", "web_search_base_url"),
    # The Mahalath seam — so `.env` alone enables/points it (no config file needed;
    # a missing config file can no longer silently disable semantic precision).
    "MAHALATH_ENABLED": ("runtime", "mahalath_enabled"),
    "MAHALATH_MONGO_URI": ("runtime", "mahalath_mongo_uri"),
    "MAHALATH_MONGO_DB": ("runtime", "mahalath_mongo_db"),
    "MAHALATH_STRICT": ("runtime", "mahalath_strict"),
}


def _apply_env_overrides(data: dict) -> dict:
    for env_var, (section, key) in _ENV_OVERRIDES.items():
        value = os.environ.get(env_var)
        if value:
            data.setdefault(section, {})[key] = value
    return data


def load_config(path: Path | str = "config.yaml") -> AppConfig:
    # An explicit TIRZAH_CONFIG env wins when the caller didn't pass a non-default
    # path, so the config location need not depend on the current directory.
    if str(path) == "config.yaml" and os.environ.get("TIRZAH_CONFIG"):
        path = os.environ["TIRZAH_CONFIG"]
    config_path = Path(path)
    if not config_path.exists():
        example_path = Path("config.example.yaml")
        config_path = example_path if example_path.exists() else config_path

    data = {}
    if config_path.exists():
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(_apply_env_overrides(data))
