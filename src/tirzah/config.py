from __future__ import annotations

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
    ollama_model: str = "gemma3:1b"
    memory_agent_model: str | None = None
    ollama_format: str | None = None
    memory_agent_ollama_format: str | None = "json"
    ollama_think: bool | str | None = False
    ollama_hide_thinking: bool = True
    ollama_base_url: str = "http://localhost:11434"
    ollama_executable: Path = Path("/mnt/c/Users/cello/AppData/Local/Programs/Ollama/ollama.exe")
    ollama_timeout_seconds: int = 180
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


class QueueConfig(BaseModel):
    max_attempts: int = 3


class RetrievalConfig(BaseModel):
    context_char_budget: int = 4000
    prompt_token_budget: int = 2000
    reserved_response_tokens: int = 500
    memory_agent_max_iterations: int = 4


class AppConfig(BaseModel):
    mongo: MongoConfig = Field(default_factory=MongoConfig)
    paths: PathConfig = Field(default_factory=PathConfig)
    runtime: RuntimeConfig = Field(default_factory=RuntimeConfig)
    queue: QueueConfig = Field(default_factory=QueueConfig)
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)


def load_config(path: Path | str = "config.yaml") -> AppConfig:
    config_path = Path(path)
    if not config_path.exists():
        example_path = Path("config.example.yaml")
        if example_path.exists():
            config_path = example_path
        else:
            return AppConfig()

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    return AppConfig.model_validate(data)
