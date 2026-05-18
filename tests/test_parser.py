from pathlib import Path

import pytest

from mnemosyne.ingestion.parser import read_text_source


def test_read_text_source_supports_markdown(tmp_path: Path) -> None:
    source = tmp_path / "note.md"
    source.write_text("# Note\n\nBody", encoding="utf-8")

    text, kind = read_text_source(source)

    assert text == "# Note\n\nBody"
    assert kind == "md"


def test_read_text_source_rejects_unknown_suffix(tmp_path: Path) -> None:
    source = tmp_path / "note.pdf"
    source.write_text("Body", encoding="utf-8")

    with pytest.raises(ValueError):
        read_text_source(source)


def test_read_text_source_preserves_plain_text_content(tmp_path: Path) -> None:
    source = tmp_path / "memory.txt"
    raw = (
        "\ufeffThe Project Gutenberg eBook of Memory\r\n"
        "\r\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK MEMORY ***\r\n"
        "Body.\r\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK MEMORY ***\r\n"
        "License text.\r\n"
    )
    source.write_text(raw, encoding="utf-8")

    text, kind = read_text_source(source)

    assert kind == "txt"
    assert text == raw
