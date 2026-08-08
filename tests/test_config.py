from pathlib import Path

from tirzah.config import load_config


def test_load_config_reads_queue_max_attempts(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
mongo:
  database: custom
queue:
  max_attempts: 5
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.mongo.database == "custom"
    assert config.queue.max_attempts == 5


def test_load_config_reads_retrieval_budget(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
retrieval:
  context_char_budget: 1234
  prompt_token_budget: 200
  reserved_response_tokens: 50
  memory_agent_max_iterations: 7
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.retrieval.context_char_budget == 1234
    assert config.retrieval.prompt_token_budget == 200
    assert config.retrieval.reserved_response_tokens == 50
    assert config.retrieval.memory_agent_max_iterations == 7


def test_load_config_reads_separate_memory_agent_model(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
runtime:
  answer_adapter: ollama_cli
  memory_agent_adapter: ollama_http
  ollama_model: final-model
  memory_agent_model: memory-model
  memory_agent_ollama_format: json
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.runtime.answer_adapter == "ollama_cli"
    assert config.runtime.memory_agent_adapter == "ollama_http"
    assert config.runtime.ollama_model == "final-model"
    assert config.runtime.memory_agent_model == "memory-model"
    assert config.runtime.memory_agent_ollama_format == "json"


def test_load_config_reads_local_profile_command(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
runtime:
  embedding_adapter: local_command
  embedding_model: local-profile
  profile_command:
    - python
    - tools/profile.py
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.runtime.embedding_adapter == "local_command"
    assert config.runtime.embedding_model == "local-profile"
    assert config.runtime.profile_command == ["python", "tools/profile.py"]


def test_load_config_reads_ingestion_adapter(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
runtime:
  ingestion_adapter: mock
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.runtime.ingestion_adapter == "mock"


def test_load_config_defaults_ingestion_adapter_to_mock(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("runtime: {}\n", encoding="utf-8")

    config = load_config(config_file)

    assert config.runtime.ingestion_adapter == "mock"


def test_default_mongo_database_is_tirzah_dev(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text("runtime: {}\n", encoding="utf-8")

    config = load_config(config_file)

    assert config.mongo.database == "tirzah_dev"


def test_load_config_accepts_unquoted_ollama_think_false(tmp_path: Path) -> None:
    config_file = tmp_path / "config.yaml"
    config_file.write_text(
        """
runtime:
  ollama_think: false
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.runtime.ollama_think is False


def test_recursive_planning_defaults_are_bounded():
    config = load_config("missing-recursive-planning-config.yaml")
    assert config.runtime.recursive_planning_enabled is True
    assert config.runtime.planning_max_revisions == 3
    assert config.runtime.planning_max_steps == 12
    # Deborah framed handoff (1.15): soft seal + framed slice on by default.
    assert config.runtime.plan_framed_execution_enabled is True
    assert config.runtime.plan_require_deborah_conformance is False
    assert config.runtime.plan_deborah_validate_profile == "full"
