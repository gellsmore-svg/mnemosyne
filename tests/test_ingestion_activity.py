from pathlib import Path

from mnemosyne.ingestion.activity import ingestion_activity_log, ingestion_activity_report
from mnemosyne.models.ingestion import IngestedNode, IngestionResult, SourceRef


def test_ingestion_activity_log_summarizes_success() -> None:
    result = IngestionResult(
        source=SourceRef(
            path="source.md",
            kind="markdown",
            checksum_sha256="abc123",
            archive_path="archive/source.md",
            origin_date="2020-01-01",
            origin_date_source="explicit_content",
            date_candidates=[{"source": "explicit_content", "value": "2020"}],
        ),
        title="Source",
        summary="A source document.",
        nodes=[
            IngestedNode(
                node_key="root",
                title="Root",
                text="Text",
                labels=["source_root", "test"],
                relations=[{"target": "other", "type": "related_to"}],
            )
        ],
        adapter="mock",
        ingestion_epoch="2026-06-01-test",
    )
    report = ingestion_activity_report(
        path=Path("source.md"),
        status="completed",
        checksum_sha256="abc123",
        result=result,
        inserted={
            "document_id": "doc1",
            "tree_id": "tree1",
            "node_count": 1,
            "edge_count": 1,
            "ingestion_epoch": "2026-06-01-test",
        },
    )

    log = ingestion_activity_log(report)

    assert log.startswith("Ingestion Activity Log")
    assert "Source dating: selected 2020-01-01" in log
    assert "Semantic processing: mock generated 1 node(s)." in log
    assert "Relationship hints: 1 proposed by ingestion." in log
    assert "Repository write: document doc1" in log


def test_ingestion_activity_log_summarizes_duplicate() -> None:
    report = ingestion_activity_report(
        path="source.md",
        status="rejected",
        checksum_sha256="abc123",
        reason="duplicate_checksum",
        message="File rejected because identical content has already been ingested.",
        details={"existing_document_id": "doc1"},
    )

    log = ingestion_activity_log(report)

    assert "Status: rejected." in log
    assert "Outcome reason: duplicate_checksum." in log
    assert "Existing document: doc1." in log
