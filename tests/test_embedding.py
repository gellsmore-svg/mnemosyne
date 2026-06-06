from __future__ import annotations

import math
from urllib import error

from mnemosyne.adapters.embedding import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    LocalCommandEmbeddingAdapter,
    MockEmbeddingAdapter,
    OllamaHttpEmbeddingAdapter,
    OllamaPowerShellEmbeddingAdapter,
    embedding_adapter,
    local_command_profile_vector,
    ollama_embedding_vector,
    source_text_hash,
)
from mnemosyne.config import RuntimeConfig


def test_mock_embedding_is_deterministic_for_same_text() -> None:
    adapter = MockEmbeddingAdapter()
    first = adapter.embed("The Taj Mahal was commissioned in 1632.")
    second = adapter.embed("The Taj Mahal was commissioned in 1632.")

    assert first == second
    assert first["adapter"] == "mock_embedding"
    assert first["model"] == "mock-deterministic-v1"
    assert first["dimensions"] == DEFAULT_EMBEDDING_DIMENSIONS
    assert first["source_text_hash"].startswith("sha256:")


def test_mock_embedding_vector_is_bounded_and_unit_norm() -> None:
    adapter = MockEmbeddingAdapter(dimensions=32)
    payload = adapter.embed("Memory architecture requirements.")

    vector = payload["vector"]
    assert len(vector) == 32
    assert payload["dimensions"] == 32
    assert all(-1.0 <= value <= 1.0 for value in vector)
    magnitude = math.sqrt(sum(value * value for value in vector))
    assert abs(magnitude - 1.0) < 1e-3


def test_mock_embedding_differs_for_different_text() -> None:
    adapter = MockEmbeddingAdapter()
    one = adapter.embed("First paragraph.")
    two = adapter.embed("Second paragraph.")

    assert one["vector"] != two["vector"]
    assert one["source_text_hash"] != two["source_text_hash"]


def test_mock_embedding_handles_empty_text() -> None:
    adapter = MockEmbeddingAdapter(dimensions=8)
    payload = adapter.embed("")

    assert len(payload["vector"]) == 8
    assert payload["source_text_hash"] == source_text_hash("")


def test_embedding_adapter_factory_honors_runtime_config_dimensions() -> None:
    config = RuntimeConfig(embedding_dimensions=24)
    adapter = embedding_adapter(config)

    assert isinstance(adapter, MockEmbeddingAdapter)
    assert adapter.dimensions == 24


def test_embedding_adapter_factory_can_create_ollama_http_adapter() -> None:
    config = RuntimeConfig(embedding_adapter="ollama_http", allow_http_ingestion_adapters=True)
    adapter = embedding_adapter(config)

    assert isinstance(adapter, OllamaHttpEmbeddingAdapter)
    assert adapter.model == "nomic-embed-text:latest"
    assert adapter.dimensions is None


def test_embedding_adapter_factory_can_create_ollama_powershell_adapter() -> None:
    config = RuntimeConfig(embedding_adapter="ollama_powershell", allow_http_ingestion_adapters=True)
    adapter = embedding_adapter(config)

    assert isinstance(adapter, OllamaPowerShellEmbeddingAdapter)
    assert adapter.model == "nomic-embed-text:latest"
    assert adapter.dimensions is None


def test_embedding_adapter_factory_can_create_local_command_adapter() -> None:
    config = RuntimeConfig(
        embedding_adapter="local_command",
        embedding_model="local-profile",
        profile_command=["profile-tool", "--json"],
    )
    adapter = embedding_adapter(config)

    assert isinstance(adapter, LocalCommandEmbeddingAdapter)
    assert adapter.model == "local-profile"
    assert adapter.command == ["profile-tool", "--json"]
    assert adapter.dimensions is None


def test_embedding_adapter_factory_rejects_local_command_without_command() -> None:
    config = RuntimeConfig(embedding_adapter="local_command")

    try:
        embedding_adapter(config)
    except ValueError as error:
        assert "runtime.profile_command" in str(error)
    else:
        raise AssertionError("Expected local command adapter to require runtime.profile_command.")


def test_embedding_adapter_factory_rejects_http_backed_adapters_by_default() -> None:
    config = RuntimeConfig(embedding_adapter="ollama_http")

    try:
        embedding_adapter(config)
    except ValueError as error:
        assert "HTTP-backed" in str(error)
        assert "ingestion or retrieval" in str(error)
    else:
        raise AssertionError("Expected HTTP-backed embedding adapter to be rejected.")


def test_embedding_adapter_factory_defaults_without_config() -> None:
    adapter = embedding_adapter()

    assert isinstance(adapter, MockEmbeddingAdapter)
    assert adapter.dimensions == DEFAULT_EMBEDDING_DIMENSIONS


def test_ollama_http_embedding_adapter_posts_to_embed_endpoint(monkeypatch) -> None:
    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def read(self):
            return b'{"embeddings": [[3, 4]]}'

    def fake_urlopen(req, timeout):
        captured["url"] = req.full_url
        captured["body"] = req.data.decode("utf-8")
        captured["timeout"] = timeout
        return FakeResponse()

    monkeypatch.setattr("mnemosyne.adapters.embedding.request.urlopen", fake_urlopen)
    config = RuntimeConfig(
        embedding_adapter="ollama_http",
        embedding_model="test-embed:latest",
        ollama_base_url="http://localhost:11434/",
        ollama_timeout_seconds=9,
    )
    adapter = OllamaHttpEmbeddingAdapter(config)

    payload = adapter.embed("Taj Mahal")

    assert captured["url"] == "http://localhost:11434/api/embed"
    assert '"model": "test-embed:latest"' in captured["body"]
    assert '"input": "Taj Mahal"' in captured["body"]
    assert captured["timeout"] == 9
    assert payload["adapter"] == "ollama_http_embedding"
    assert payload["model"] == "test-embed:latest"
    assert payload["dimensions"] == 2
    assert payload["vector"] == [0.6, 0.8]
    assert adapter.dimensions == 2


def test_ollama_powershell_embedding_adapter_posts_payload_over_stdin(monkeypatch) -> None:
    captured = {}

    def fake_run(command, input, check, capture_output, text, timeout):
        captured["command"] = command
        captured["input"] = input
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return type("Completed", (), {"returncode": 0, "stdout": '{"embeddings": [[3, 4]]}', "stderr": ""})()

    monkeypatch.setattr("mnemosyne.adapters.embedding.subprocess.run", fake_run)
    config = RuntimeConfig(
        embedding_adapter="ollama_powershell",
        embedding_model="test-embed:latest",
        ollama_timeout_seconds=9,
    )
    adapter = OllamaPowerShellEmbeddingAdapter(config)

    payload = adapter.embed("Taj Mahal")

    assert captured["command"][:3] == ["powershell.exe", "-NoProfile", "-NonInteractive"]
    assert "Invoke-RestMethod" in captured["command"][-1]
    assert "http://localhost:11434/api/embed" in captured["command"][-1]
    assert '"model": "test-embed:latest"' in captured["input"]
    assert '"input": "Taj Mahal"' in captured["input"]
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 9
    assert payload["adapter"] == "ollama_powershell_embedding"
    assert payload["model"] == "test-embed:latest"
    assert payload["dimensions"] == 2
    assert payload["vector"] == [0.6, 0.8]
    assert adapter.dimensions == 2


def test_ollama_powershell_embedding_adapter_keeps_text_out_of_command(monkeypatch) -> None:
    captured = {}
    tricky_text = 'quote"; $(Get-Process); newline\ntext'

    def fake_run(command, input, check, capture_output, text, timeout):
        captured["command"] = command
        captured["input"] = input
        return type("Completed", (), {"returncode": 0, "stdout": '{"embeddings": [[1, 0]]}', "stderr": ""})()

    monkeypatch.setattr("mnemosyne.adapters.embedding.subprocess.run", fake_run)
    adapter = OllamaPowerShellEmbeddingAdapter(RuntimeConfig(embedding_adapter="ollama_powershell"))

    adapter.embed(tricky_text)

    assert tricky_text not in " ".join(captured["command"])
    assert 'quote\\"; $(Get-Process); newline\\ntext' in captured["input"]


def test_ollama_http_embedding_adapter_reports_url_errors(monkeypatch) -> None:
    def fake_urlopen(_req, timeout):
        del timeout
        raise error.URLError("connection refused")

    monkeypatch.setattr("mnemosyne.adapters.embedding.request.urlopen", fake_urlopen)
    adapter = OllamaHttpEmbeddingAdapter(RuntimeConfig(embedding_adapter="ollama_http"))

    try:
        adapter.embed("text")
    except RuntimeError as exc:
        assert "Ollama embedding request failed" in str(exc)
        assert "nomic-embed-text:latest" in str(exc)
    else:
        raise AssertionError("Expected embedding adapter to report URL failure.")


def test_ollama_powershell_embedding_adapter_reports_process_errors(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        return type(
            "Completed",
            (),
            {"returncode": 1, "stdout": "", "stderr": "connection failed"},
        )()

    monkeypatch.setattr("mnemosyne.adapters.embedding.subprocess.run", fake_run)
    adapter = OllamaPowerShellEmbeddingAdapter(RuntimeConfig(embedding_adapter="ollama_powershell"))

    try:
        adapter.embed("text")
    except RuntimeError as exc:
        assert "Ollama PowerShell embedding request failed" in str(exc)
        assert "connection failed" in str(exc)
    else:
        raise AssertionError("Expected PowerShell embedding adapter to report process failure.")


def test_local_command_embedding_adapter_uses_stdin_json(monkeypatch) -> None:
    captured = {}
    tricky_text = 'quote"; $(Get-Process); newline\ntext'

    def fake_run(command, input, check, capture_output, text, timeout):
        captured["command"] = command
        captured["input"] = input
        captured["check"] = check
        captured["capture_output"] = capture_output
        captured["text"] = text
        captured["timeout"] = timeout
        return type("Completed", (), {"returncode": 0, "stdout": '{"vector": [3, 4]}', "stderr": ""})()

    monkeypatch.setattr("mnemosyne.adapters.embedding.subprocess.run", fake_run)
    config = RuntimeConfig(
        embedding_adapter="local_command",
        embedding_model="local-profile",
        profile_command=["profile-tool", "--json"],
        ollama_timeout_seconds=11,
    )
    adapter = LocalCommandEmbeddingAdapter(config)

    payload = adapter.embed(tricky_text)

    assert captured["command"] == ["profile-tool", "--json"]
    assert tricky_text not in " ".join(captured["command"])
    assert 'quote\\"; $(Get-Process); newline\\ntext' in captured["input"]
    assert '"model": "local-profile"' in captured["input"]
    assert captured["check"] is False
    assert captured["capture_output"] is True
    assert captured["text"] is True
    assert captured["timeout"] == 11
    assert payload["adapter"] == "local_command_profile"
    assert payload["model"] == "local-profile"
    assert payload["dimensions"] == 2
    assert payload["vector"] == [0.6, 0.8]
    assert adapter.dimensions == 2


def test_local_command_embedding_adapter_requires_command() -> None:
    adapter = LocalCommandEmbeddingAdapter(RuntimeConfig(embedding_adapter="local_command"))

    try:
        adapter.embed("text")
    except RuntimeError as exc:
        assert "runtime.profile_command" in str(exc)
    else:
        raise AssertionError("Expected local command adapter to require a configured command.")


def test_local_command_embedding_adapter_reports_process_errors(monkeypatch) -> None:
    def fake_run(*_args, **_kwargs):
        return type("Completed", (), {"returncode": 1, "stdout": "", "stderr": "tool failed"})()

    monkeypatch.setattr("mnemosyne.adapters.embedding.subprocess.run", fake_run)
    adapter = LocalCommandEmbeddingAdapter(
        RuntimeConfig(embedding_adapter="local_command", profile_command=["profile-tool"])
    )

    try:
        adapter.embed("text")
    except RuntimeError as exc:
        assert "Local command profile adapter failed" in str(exc)
        assert "tool failed" in str(exc)
    else:
        raise AssertionError("Expected local command adapter to report process failure.")


def test_local_command_embedding_adapter_worker_reuses_process(monkeypatch) -> None:
    captured = {"writes": [], "flushes": 0, "popen_calls": 0}

    class FakeStdin:
        def write(self, value):
            captured["writes"].append(value)

        def flush(self):
            captured["flushes"] += 1

        def close(self):
            captured["closed"] = True

    class FakeStdout:
        def __init__(self):
            self.lines = iter(['{"vector": [3, 4]}\n', '{"vector": [0, 5]}\n'])

        def readline(self):
            return next(self.lines)

    class FakeStderr:
        def read(self):
            return ""

    class FakeProcess:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = FakeStdout()
            self.stderr = FakeStderr()

        def poll(self):
            return None

        def wait(self, timeout=None):
            del timeout
            return 0

        def kill(self):
            captured["killed"] = True

    def fake_popen(command, stdin, stdout, stderr, text, bufsize):
        captured["popen_calls"] += 1
        captured["command"] = command
        captured["stdin_pipe"] = stdin
        captured["stdout_pipe"] = stdout
        captured["stderr_pipe"] = stderr
        captured["text"] = text
        captured["bufsize"] = bufsize
        return FakeProcess()

    monkeypatch.setattr("mnemosyne.adapters.embedding.subprocess.Popen", fake_popen)
    monkeypatch.setattr(
        "mnemosyne.adapters.embedding.select.select",
        lambda readable, _writable, _errors, timeout: (readable, [], []),
    )
    config = RuntimeConfig(
        embedding_adapter="local_command",
        embedding_model="local-profile",
        profile_command=["profile-tool", "--worker"],
        profile_command_mode="worker",
    )
    adapter = LocalCommandEmbeddingAdapter(config)

    first = adapter.embed("first")
    second = adapter.embed("second")
    adapter.close()

    assert captured["popen_calls"] == 1
    assert captured["command"] == ["profile-tool", "--worker"]
    assert '"text": "first"' in captured["writes"][0]
    assert '"text": "second"' in captured["writes"][2]
    assert captured["flushes"] == 2
    assert first["vector"] == [0.6, 0.8]
    assert second["vector"] == [0.0, 1.0]


def test_local_command_embedding_adapter_worker_reports_json_errors(monkeypatch) -> None:
    class FakeStdin:
        def write(self, _value):
            return None

        def flush(self):
            return None

        def close(self):
            return None

    class FakeStdout:
        def readline(self):
            return '{"error": {"code": 3, "message": "model mismatch"}}\n'

    class FakeProcess:
        stdin = FakeStdin()
        stdout = FakeStdout()
        stderr = None

        def poll(self):
            return None

        def wait(self, timeout=None):
            del timeout
            return 0

        def kill(self):
            return None

    monkeypatch.setattr(
        "mnemosyne.adapters.embedding.subprocess.Popen",
        lambda *_args, **_kwargs: FakeProcess(),
    )
    monkeypatch.setattr(
        "mnemosyne.adapters.embedding.select.select",
        lambda readable, _writable, _errors, timeout: (readable, [], []),
    )
    adapter = LocalCommandEmbeddingAdapter(
        RuntimeConfig(
            embedding_adapter="local_command",
            profile_command=["profile-tool", "--worker"],
            profile_command_mode="worker",
        )
    )

    try:
        adapter.embed("text")
    except RuntimeError as exc:
        assert "Local command profile worker failed" in str(exc)
        assert "model mismatch" in str(exc)
    else:
        raise AssertionError("Expected worker JSON error to be reported.")


def test_ollama_embedding_vector_accepts_legacy_shape() -> None:
    assert ollama_embedding_vector({"embedding": [1, 2, 3]}) == [1.0, 2.0, 3.0]


def test_local_command_profile_vector_accepts_vector_shape() -> None:
    assert local_command_profile_vector({"vector": [1, 2, 3]}) == [1.0, 2.0, 3.0]


def test_ollama_embedding_vector_rejects_malformed_values() -> None:
    assert ollama_embedding_vector({"embeddings": [["not-a-number"]]}) == []
