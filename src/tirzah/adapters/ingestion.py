from __future__ import annotations

from pathlib import Path
from typing import Protocol

from tirzah.adapters.mock import MockIngestionAdapter
from tirzah.config import RuntimeConfig
from tirzah.models.ingestion import IngestionResult


class IngestionAdapter(Protocol):
    def process(
        self,
        path: Path,
        text: str,
        source_kind: str,
        extra_labels: list[str] | None = None,
    ) -> IngestionResult:
        ...


def ingestion_adapter(config: RuntimeConfig | None = None) -> IngestionAdapter:
    name = getattr(config, "ingestion_adapter", "mock") if config else "mock"
    if name == "mock":
        return MockIngestionAdapter()
    raise ValueError(f"Unknown ingestion adapter: {name}")
