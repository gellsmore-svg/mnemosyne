from __future__ import annotations

import math

from mnemosyne.adapters.embedding import (
    DEFAULT_EMBEDDING_DIMENSIONS,
    MockEmbeddingAdapter,
    embedding_adapter,
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


def test_embedding_adapter_factory_defaults_without_config() -> None:
    adapter = embedding_adapter()

    assert isinstance(adapter, MockEmbeddingAdapter)
    assert adapter.dimensions == DEFAULT_EMBEDDING_DIMENSIONS
