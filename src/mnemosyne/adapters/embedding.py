from __future__ import annotations

import hashlib
import math
import struct
from typing import Any

DEFAULT_EMBEDDING_DIMENSIONS = 16
MAX_EMBEDDING_DIMENSIONS = 256


class MockEmbeddingAdapter:
    """Deterministic, dependency-free embedding adapter.

    Produces a bounded unit-norm vector derived from a SHA-256 expansion of the
    source text. The same text always yields the same vector, so stored
    embeddings are reproducible without any external model, GPU, or network
    call. This is the substrate landing point for real local embedding models;
    it intentionally does not change retrieval ranking.
    """

    name = "mock_embedding"
    model = "mock-deterministic-v1"

    def __init__(self, dimensions: int = DEFAULT_EMBEDDING_DIMENSIONS) -> None:
        self.dimensions = bounded_dimensions(dimensions)

    def embed(self, text: str) -> dict[str, Any]:
        normalized = text or ""
        return {
            "adapter": self.name,
            "model": self.model,
            "dimensions": self.dimensions,
            "vector": deterministic_vector(normalized, self.dimensions),
            "source_text_hash": source_text_hash(normalized),
        }


def embedding_adapter(config: Any | None = None) -> MockEmbeddingAdapter:
    if config is None:
        return default_embedding_adapter()
    name = getattr(config, "embedding_adapter", "mock")
    dimensions = getattr(config, "embedding_dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
    if name == "mock":
        return MockEmbeddingAdapter(dimensions=dimensions)
    raise ValueError(f"Unknown embedding adapter: {name}")


def deterministic_vector(text: str, dimensions: int) -> list[float]:
    encoded = text.encode("utf-8")
    raw: list[float] = []
    counter = 0
    while len(raw) < dimensions:
        digest = hashlib.sha256(encoded + counter.to_bytes(4, "big")).digest()
        for index in range(0, len(digest), 4):
            if len(raw) >= dimensions:
                break
            (value,) = struct.unpack(">I", digest[index : index + 4])
            raw.append(value / 0xFFFFFFFF * 2.0 - 1.0)
        counter += 1
    return l2_normalize(raw[:dimensions])


def l2_normalize(vector: list[float]) -> list[float]:
    magnitude = math.sqrt(sum(value * value for value in vector))
    if magnitude == 0:
        return [0.0 for _ in vector]
    return [round(value / magnitude, 6) for value in vector]


def source_text_hash(text: str) -> str:
    return "sha256:" + hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def bounded_dimensions(value: Any, default: int = DEFAULT_EMBEDDING_DIMENSIONS) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, MAX_EMBEDDING_DIMENSIONS))


_DEFAULT_ADAPTER = MockEmbeddingAdapter()


def default_embedding_adapter() -> MockEmbeddingAdapter:
    return _DEFAULT_ADAPTER
