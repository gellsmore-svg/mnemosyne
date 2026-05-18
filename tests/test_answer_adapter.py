import subprocess

import pytest

from mnemosyne.adapters.answer import (
    MockAnswerAdapter,
    OllamaCliAnswerAdapter,
    OllamaHttpAnswerAdapter,
    clean_ollama_output,
    repair_duplicate_wrap_fragments,
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


def test_clean_ollama_output_applies_cursor_rewrites() -> None:
    assert clean_ollama_output("prompte\x1b[7D\x1b[Kprompted\n") == "prompted"


def test_clean_ollama_output_repairs_duplicate_wrap_fragments() -> None:
    raw = (
        "I understand you are asking about the MongoDB instance used when I am prompte\n"
        "prompted to answer your questions. However, the u\n"
        "underlying system includes Mo\n"
        "MongoDB."
    )

    assert clean_ollama_output(raw) == (
        "I understand you are asking about the MongoDB instance used when I am "
        "prompted to answer your questions. However, the underlying system includes MongoDB."
    )


def test_repair_duplicate_wrap_fragments_leaves_intentional_newlines() -> None:
    assert repair_duplicate_wrap_fragments("first line\nsecond line") == "first line\nsecond line"


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

    assert captured["cmd"][-4:] == ["--nowordwrap", "--think=false", "--hidethinking", "gemma3:1b"]
    assert captured["input"] == "prompt body"
    assert answer["answer"] == "answer"


def test_ollama_cli_adapter_can_request_json_format(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout='{"ok":true}\n', stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    answer = OllamaCliAnswerAdapter(
        RuntimeConfig(ollama_model="qwen3.6:latest", ollama_format="json")
    ).answer(
        {
            "prompt_text": "return json",
            "context_metadata": {"included": []},
        }
    )

    assert captured["cmd"] == [
        str(RuntimeConfig().ollama_executable),
        "run",
        "--nowordwrap",
        "--format",
        "json",
        "--think=false",
        "--hidethinking",
        "qwen3.6:latest",
    ]
    assert answer["answer"] == '{"ok":true}'


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


def test_ollama_cli_adapter_retries_without_optional_flags_for_old_ollama(monkeypatch) -> None:
    captured = []

    def fake_run(cmd, **kwargs):
        captured.append(cmd)
        if len(captured) == 1:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="Error: unknown flag: --think",
            )
        return subprocess.CompletedProcess(cmd, 0, stdout="answer\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    answer = OllamaCliAnswerAdapter(RuntimeConfig(ollama_model="gemma3:1b")).answer(
        {
            "prompt_text": "prompt body",
            "context_metadata": {"included": []},
        }
    )

    assert "--think=false" in captured[0]
    assert "--hidethinking" in captured[0]
    assert captured[1] == [
        str(RuntimeConfig().ollama_executable),
        "run",
        "--nowordwrap",
        "gemma3:1b",
    ]
    assert answer["answer"] == "answer"


def test_ollama_cli_adapter_wraps_fallback_failure(monkeypatch) -> None:
    def fake_run(cmd, **kwargs):
        if "--think=false" in cmd:
            raise subprocess.CalledProcessError(
                returncode=1,
                cmd=cmd,
                stderr="Error: unknown flag: --think",
            )
        raise subprocess.CalledProcessError(
            returncode=2,
            cmd=cmd,
            stderr="Error: model failed",
        )

    monkeypatch.setattr(subprocess, "run", fake_run)

    with pytest.raises(RuntimeError, match="model failed"):
        OllamaCliAnswerAdapter(RuntimeConfig(ollama_model="gemma3:1b")).answer(
            {
                "prompt_text": "prompt body",
                "context_metadata": {"included": []},
            }
        )


def test_ollama_cli_adapter_can_omit_optional_flags(monkeypatch) -> None:
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="answer\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    OllamaCliAnswerAdapter(
        RuntimeConfig(ollama_think=None, ollama_hide_thinking=False)
    ).answer(
        {
            "prompt_text": "prompt body",
            "context_metadata": {"included": []},
        }
    )

    assert "--think=false" not in captured["cmd"]
    assert "--hidethinking" not in captured["cmd"]


def test_ollama_http_adapter_sends_format_and_think(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return None

        def read(self):
            return b'{"response":"answer"}'

    def fake_urlopen(req, timeout):
        captured["body"] = req.data
        captured["timeout"] = timeout
        return FakeResponse()

    import mnemosyne.adapters.answer as answer_module

    monkeypatch.setattr(answer_module.request, "urlopen", fake_urlopen)

    answer = OllamaHttpAnswerAdapter(
        RuntimeConfig(ollama_format="json", ollama_think=False)
    ).answer(
        {
            "prompt_text": "prompt body",
            "context_metadata": {"included": []},
        }
    )

    assert b'"format": "json"' in captured["body"]
    assert b'"think": false' in captured["body"]
    assert captured["timeout"] == RuntimeConfig().ollama_timeout_seconds
    assert answer["answer"] == "answer"
