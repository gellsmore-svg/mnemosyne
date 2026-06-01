from __future__ import annotations

import math
from urllib import error

from mnemosyne.adapters.embedding import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    MockEmbeddingAdapter,
    OllamaHttpEmbeddingAdapter,
    embedding_adapter,
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
    config = RuntimeConfig(embedding_adapter="ollama_http")
    adapter = embedding_adapter(config)

    assert isinstance(adapter, OllamaHttpEmbeddingAdapter)
    assert adapter.model == "nomic-embed-text:latest"
    assert adapter.dimensions is None


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


def test_ollama_embedding_vector_accepts_legacy_shape() -> None:
    assert ollama_embedding_vector({"embedding": [1, 2, 3]}) == [1.0, 2.0, 3.0]


def test_ollama_embedding_vector_rejects_malformed_values() -> None:
    assert ollama_embedding_vector({"embeddings": [["not-a-number"]]}) == []
