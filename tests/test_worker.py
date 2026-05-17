from pathlib import Path

from mnemosyne.ingestion.worker import discover_sources


def test_discover_sources_only_returns_supported_files(tmp_path: Path) -> None:
    markdown = tmp_path / "a.md"
    text = tmp_path / "b.txt"
    unsupported = tmp_path / "c.pdf"
    markdown.write_text("a", encoding="utf-8")
    text.write_text("b", encoding="utf-8")
    unsupported.write_text("c", encoding="utf-8")

    assert discover_sources(tmp_path) == [markdown, text]


def test_discover_sources_handles_missing_folder(tmp_path: Path) -> None:
    assert discover_sources(tmp_path / "missing") == []
