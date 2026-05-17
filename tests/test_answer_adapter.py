import subprocess

import pytest

from mnemosyne.adapters.answer import (
    MockAnswerAdapter,
    OllamaCliAnswerAdapter,
    clean_ollama_output,
    summarize_context_text,
)
from mnemosyne.config import RuntimeConfig


def test_summarize_context_text_ignores_metadata_lines() -> None:
    summary = summarize_context_text(
        "# Mnemosyne Context\n"
        "Document: Doc\n"
        "Document ID: doc1\n"
        "## Context Records\n"
        "- Node ID: node1\n"
        "Actual content.\n"
    )

    assert summary == "Actual content."


def test_mock_answer_adapter_returns_used_nodes() -> None:
    answer = MockAnswerAdapter().answer(
        {
            "context_text": "Useful context.",
            "context_metadata": {
                "included": [{"node_id": "node1"}, {"node_id": "node2"}],
            },
        }
    )

    assert answer["adapter"] == "mock_answer"
    assert answer["used_node_ids"] == ["node1", "node2"]


def test_clean_ollama_output_strips_spinner_and_ansi() -> None:
    assert clean_ollama_output("\x1b[?25l⠙ \x1b[Kmnemosyne-ok\n") == "mnemosyne-ok"


def test_ollama_cli_adapter_passes_prompt_via_stdin(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["input"] = kwargs["input"]
        return subprocess.CompletedProcess(cmd, 0, stdout="answer\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    answer = OllamaCliAnswerAdapter(RuntimeConfig(ollama_model="gemma3:1b")).answer(
        {
            "prompt_text": "prompt body",
            "context_metadata": {"included": []},
        }
    )

    assert captured["cmd"][-1] == "gemma3:1b"
    assert captured["input"] == "prompt body"
    assert answer["answer"] == "answer"


def test_ollama_cli_adapter_reports_timeout(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        raise subprocess.TimeoutExpired(cmd, timeout=3)

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(TimeoutError, match="timed out"):
        OllamaCliAnswerAdapter(RuntimeConfig(ollama_timeout_seconds=3)).answer(
            {
                "prompt_text": "prompt body",
                "context_metadata": {"included": []},
            }
        )
