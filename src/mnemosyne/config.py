from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field


class MongoConfig(BaseModel):
    uri: str = "mongodb://localhost:27017"
    database: str = "mnemosyne_dev"


class PathConfig(BaseModel):
    ingest: Path = Path("data/ingest")
    archive: Path = Path("data/archive")
    dead_letter: Path = Path("data/dead_letter")
    staging: Path = Path("data/staging")


class RuntimeConfig(BaseModel):
    model_adapter: str = "mock"
    answer_adapter: str = "ollama_cli"
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
