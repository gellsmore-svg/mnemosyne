from __future__ import annotations

import hashlib
import json
import math
import struct
from typing import Any
from urllib import error, request

DEFAULT_EMBEDDING_DIMENSIONS = 16
MAX_EMBEDDING_DIMENSIONS = 256
DEFAULT_OLLAMA_EMBEDDING_MODEL = "nomic-embed-text:latest"


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


class OllamaHttpEmbeddingAdapter:
    name = "ollama_http_embedding"

    def __init__(self, config: Any) -> None:
        self.config = config
        self.model = getattr(config, "embedding_model", None) or DEFAULT_OLLAMA_EMBEDDING_MODEL
        self.dimensions: int | None = None

    def embed(self, text: str) -> dict[str, Any]:
        normalized = text or ""
        vector = l2_normalize(self._embedding_vector(normalized))
        self.dimensions = len(vector)
        return {
            "adapter": self.name,
            "model": self.model,
            "dimensions": len(vector),
            "vector": vector,
            "source_text_hash": source_text_hash(normalized),
        }

    def _embedding_vector(self, text: str) -> list[float]:
        endpoint = f"{str(self.config.ollama_base_url).rstrip('/')}/api/embed"
        payload = json.dumps(
            {
                "model": self.model,
                "input": text,
            }
        ).encode("utf-8")
        req = request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.config.ollama_timeout_seconds) as response:
                data = json.loads(response.read().decode("utf-8"))
        except TimeoutError as exception:
            raise TimeoutError(
                f"Ollama embedding request timed out after "
                f"{self.config.ollama_timeout_seconds}s while running {self.model}."
            ) from exception
        except error.URLError as exception:
            raise RuntimeError(
                f"Ollama embedding request failed for {self.model} at {endpoint}: "
                f"{exception.reason}"
            ) from exception
        except json.JSONDecodeError as exception:
            raise RuntimeError(
                f"Ollama embedding request returned invalid JSON for {self.model}."
            ) from exception
        vector = ollama_embedding_vector(data)
        if not vector:
            raise RuntimeError(f"Ollama embedding request returned no vector for {self.model}.")
        return vector


def embedding_adapter(config: Any | None = None) -> MockEmbeddingAdapter | OllamaHttpEmbeddingAdapter:
    if config is None:
        return default_embedding_adapter()
    name = getattr(config, "embedding_adapter", "mock")
    dimensions = getattr(config, "embedding_dimensions", DEFAULT_EMBEDDING_DIMENSIONS)
    if name == "mock":
        return MockEmbeddingAdapter(dimensions=dimensions)
    if name == "ollama_http":
        return OllamaHttpEmbeddingAdapter(config)
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


def ollama_embedding_vector(data: dict[str, Any]) -> list[float]:
    if isinstance(data.get("embedding"), list):
        return parse_embedding_vector(data["embedding"])
    embeddings = data.get("embeddings")
    if isinstance(embeddings, list) and embeddings:
        return parse_embedding_vector(embeddings[0])
    return []


def parse_embedding_vector(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    try:
        vector = [float(item) for item in value]
    except (TypeError, ValueError):
        return []
    if not vector or not all(math.isfinite(item) for item in vector):
        return []
    return vector


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
