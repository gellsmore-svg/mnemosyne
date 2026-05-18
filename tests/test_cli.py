from pathlib import Path

from bson import ObjectId

from mnemosyne.cli import (
    discover_folder_sources,
    document_ids_for_label,
    existing_document_extra_labels,
    rebuild_document_from_existing_source,
)


def test_discover_folder_sources_finds_markdown_and_text(tmp_path: Path) -> None:
    root = tmp_path / "source"
    root.mkdir()
    (root / "a.md").write_text("a", encoding="utf-8")
    (root / "b.txt").write_text("b", encoding="utf-8")
    (root / "c.json").write_text("{}", encoding="utf-8")

    assert [path.name for path in discover_folder_sources(root)] == ["a.md", "b.txt"]


def test_discover_folder_sources_skips_git_directory(tmp_path: Path) -> None:
    root = tmp_path / "source"
    git_dir = root / ".git"
    git_dir.mkdir(parents=True)
    (git_dir / "ignored.md").write_text("ignored", encoding="utf-8")
    (root / "included.md").write_text("included", encoding="utf-8")

    assert [path.name for path in discover_folder_sources(root)] == ["included.md"]


def test_existing_document_extra_labels_excludes_structural_labels() -> None:
    document_id = ObjectId()
    db = FakeDb(
        [
            {"document_id": document_id, "labels": ["source_root", "memory_reference"]},
            {"document_id": document_id, "labels": ["source_chunk", "external_corpus"]},
        ]
    )

    assert existing_document_extra_labels(db, str(document_id)) == [
        "external_corpus",
        "memory_reference",
    ]


def test_document_ids_for_label_returns_sorted_strings() -> None:
    first = ObjectId()
    second = ObjectId()
    db = FakeDb(
        [
            {"document_id": second, "labels": ["ams_domain"]},
            {"document_id": first, "labels": ["ams_domain"]},
            {"document_id": ObjectId(), "labels": ["other"]},
        ]
    )

    assert document_ids_for_label(db, "ams_domain") == sorted([str(first), str(second)])


def test_rebuild_document_uses_original_source_path_for_adapter_title(
    tmp_path: Path,
    monkeypatch,
) -> None:
    archive = tmp_path / "abc123.txt"
    archive.write_text("plain text", encoding="utf-8")
    document_id = ObjectId()
    db = FakeDb(
        [],
        document={
            "document_id": str(document_id),
            "source": {
                "path": "data/ingest/original-name.txt",
                "archive_path": str(archive),
                "checksum_sha256": "abc123",
            },
        },
    )
    captured = {}

    def fake_rebuild(_db, _document_id, result):
        captured["title"] = result.title
        return {"document_id": str(document_id), "replaced": True}

    monkeypatch.setattr("mnemosyne.cli.get_document", lambda _db, _document_id: db.document)
    monkeypatch.setattr("mnemosyne.cli.rebuild_document", fake_rebuild)

    result = rebuild_document_from_existing_source(db, str(document_id))

    assert result["ok"] is True
    assert captured["title"] == "original-name"


class FakeCollection:
    def __init__(self, rows: list[dict]) -> None:
        self.rows = rows

    def find(self, query: dict, _projection: dict) -> list[dict]:
        return [row for row in self.rows if row["document_id"] == query["document_id"]]

    def distinct(self, field: str, query: dict) -> list:
        values = []
        for row in self.rows:
            if query["labels"] not in row.get("labels", []):
                continue
            value = row[field]
            if value not in values:
                values.append(value)
        return values


class FakeDb:
    def __init__(self, nodes: list[dict], document: dict | None = None) -> None:
        self.nodes = FakeCollection(nodes)
        self.document = document
