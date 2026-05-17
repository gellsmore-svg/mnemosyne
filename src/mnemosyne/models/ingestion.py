from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from pydantic import BaseModel, Field


SCHEMA_VERSION = 1
DEFAULT_ENDORSEMENT_LABEL = "unreviewed"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class SourceRef(BaseModel):
    path: str
    kind: str
    checksum_sha256: str | None = None
    archive_path: str | None = None


class Provenance(BaseModel):
    source_path: str
    source_checksum_sha256: str | None = None
    archive_path: str | None = None
    endorsement_label: str = DEFAULT_ENDORSEMENT_LABEL
    adapter: str = "mock"


class IngestedNode(BaseModel):
    node_key: str
    parent_key: str | None = None
    title: str
    text: str
    labels: list[str] = Field(default_factory=list)
    endorsement_label: str = DEFAULT_ENDORSEMENT_LABEL
    metadata: dict[str, Any] = Field(default_factory=dict)


class IngestionResult(BaseModel):
    source: SourceRef
    title: str
    summary: str
    tree_label: str = "source"
    nodes: list[IngestedNode]
    adapter: str = "mock"
    created_at: datetime = Field(default_factory=utc_now)


class DocumentRecord(BaseModel):
    schema_version: int = SCHEMA_VERSION
    title: str
    summary: str
    source: SourceRef
    created_at: datetime
    updated_at: datetime


class TreeRecord(BaseModel):
    schema_version: int = SCHEMA_VERSION
    document_id: Any
    label: str
    kind: str = "source_document"
    created_at: datetime
    updated_at: datetime


class NodeRecord(BaseModel):
    schema_version: int = SCHEMA_VERSION
    document_id: Any
    tree_id: Any
    parent_id: Any | None = None
    node_key: str
    parent_key: str | None = None
    order: int
    title: str
    text: str
    labels: list[str] = Field(default_factory=list)
    endorsement_label: str = DEFAULT_ENDORSEMENT_LABEL
    provenance: Provenance
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime
