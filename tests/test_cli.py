from pathlib import Path

from mnemosyne.cli import discover_folder_sources


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
