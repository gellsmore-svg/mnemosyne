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


def test_read_text_source_prepares_gutenberg_plain_text(tmp_path: Path) -> None:
    source = tmp_path / "memory.txt"
    source.write_text(
        "\ufeffThe Project Gutenberg eBook of Memory: How to Develop, Train, and Use It\r\n"
        "\r\n"
        "Title: Memory: How to Develop, Train, and Use It\r\n"
        "\r\n"
        "*** START OF THE PROJECT GUTENBERG EBOOK MEMORY: HOW TO DEVELOP, TRAIN, AND USE IT ***\r\n"
        "\r\n"
        "MEMORY\r\n"
        "\r\n"
        "Useful body.\r\n"
        "\r\n"
        "*** END OF THE PROJECT GUTENBERG EBOOK MEMORY: HOW TO DEVELOP, TRAIN, AND USE IT ***\r\n"
        "License text.\r\n",
        encoding="utf-8",
    )

    text, kind = read_text_source(source)

    assert kind == "txt"
    assert text.startswith("# Memory: How to Develop, Train, and Use It\n\nMEMORY")
    assert "Project Gutenberg eBook" not in text
    assert "License text" not in text


def test_read_text_source_keeps_plain_text_without_title(tmp_path: Path) -> None:
    source = tmp_path / "note.txt"
    source.write_text("First line\r\n\r\nSecond line", encoding="utf-8")

    text, kind = read_text_source(source)

    assert kind == "txt"
    assert text == "First line\n\nSecond line"
