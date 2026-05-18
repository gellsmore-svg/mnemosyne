from pathlib import Path

from mnemosyne.config import load_config


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
""",
        encoding="utf-8",
    )

    config = load_config(config_file)

    assert config.runtime.answer_adapter == "ollama_cli"
    assert config.runtime.memory_agent_adapter == "ollama_http"
    assert config.runtime.ollama_model == "final-model"
    assert config.runtime.memory_agent_model == "memory-model"
