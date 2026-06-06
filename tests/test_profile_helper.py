from __future__ import annotations

import importlib.util
import io
import json
import sys
from pathlib import Path


def load_profile_helper():
    path = Path(__file__).resolve().parents[1] / "tools" / "profile_helper.py"
    spec = importlib.util.spec_from_file_location("profile_helper", path)
    module = importlib.util.module_from_spec(spec)
    assert spec and spec.loader
    spec.loader.exec_module(module)
    return module


def run_helper(module, monkeypatch, stdin: str) -> tuple[int, str, str]:
    stdout = io.StringIO()
    stderr = io.StringIO()
    monkeypatch.setattr(sys, "stdin", io.StringIO(stdin))
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", stderr)

    code = module.main(["profile_helper.py"])

    return code, stdout.getvalue(), stderr.getvalue()


def test_profile_helper_returns_vector_json(monkeypatch) -> None:
    module = load_profile_helper()
    monkeypatch.setattr(module, "embed_text", lambda text: [3.0, 4.0] if text == "Taj Mahal" else [])

    code, stdout, stderr = run_helper(
        module,
        monkeypatch,
        json.dumps({"model": module.SUPPORTED_MODEL, "text": "Taj Mahal"}),
    )

    assert code == 0
    assert stderr == ""
    assert json.loads(stdout) == {"vector": [3.0, 4.0]}


def test_profile_helper_rejects_empty_stdin(monkeypatch) -> None:
    module = load_profile_helper()

    code, stdout, stderr = run_helper(module, monkeypatch, "")

    assert code == 2
    assert stdout == ""
    assert "empty stdin" in stderr


def test_profile_helper_rejects_model_mismatch(monkeypatch) -> None:
    module = load_profile_helper()

    code, stdout, stderr = run_helper(
        module,
        monkeypatch,
        json.dumps({"model": "other-model", "text": "Taj Mahal"}),
    )

    assert code == 3
    assert stdout == ""
    assert "does not match" in stderr


def test_profile_helper_reports_known_embedding_error(monkeypatch) -> None:
    module = load_profile_helper()

    def fail_embed(_text: str):
        raise RuntimeError("fastembed is not installed")

    monkeypatch.setattr(module, "embed_text", fail_embed)

    code, stdout, stderr = run_helper(
        module,
        monkeypatch,
        json.dumps({"model": module.SUPPORTED_MODEL, "text": "Taj Mahal"}),
    )

    assert code == 4
    assert stdout == ""
    assert "fastembed is not installed" in stderr
