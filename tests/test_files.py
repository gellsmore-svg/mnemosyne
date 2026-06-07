from pathlib import Path

from tirzah.ingestion.files import archive_source, move_request_file, sha256_file


def test_sha256_file_is_content_based(tmp_path: Path) -> None:
    first = tmp_path / "first.md"
    second = tmp_path / "second.md"
    first.write_text("same content", encoding="utf-8")
    second.write_text("same content", encoding="utf-8")

    assert sha256_file(first) == sha256_file(second)


def test_archive_source_copies_to_checksum_path(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    archive = tmp_path / "archive"
    source.write_text("content", encoding="utf-8")
    checksum = sha256_file(source)

    archived = archive_source(source, archive, checksum)

    assert archived.exists()
    assert archived.read_text(encoding="utf-8") == "content"
    assert archived.name == f"{checksum}.md"
    assert archived.parent.name == checksum[:2]


def test_move_request_file_moves_to_target_dir(tmp_path: Path) -> None:
    source = tmp_path / "source.md"
    target = tmp_path / "processed"
    source.write_text("content", encoding="utf-8")
    checksum = sha256_file(source)

    moved = move_request_file(source, target, checksum)

    assert not source.exists()
    assert moved.exists()
    assert moved.read_text(encoding="utf-8") == "content"
