from pathlib import Path

from mnemosyne.adapters.mock import MockIngestionAdapter


def test_mock_adapter_uses_first_heading_as_title() -> None:
    result = MockIngestionAdapter().process(
        Path("sample.md"),
        "# Project Memory\n\nFirst paragraph.\n\nSecond paragraph.",
        "md",
    )

    assert result.title == "Project Memory"
    assert result.nodes[0].node_key == "root"
    assert result.nodes[0].labels == ["source_root"]
    assert result.nodes[1].parent_key == "root"
    assert result.nodes[2].parent_key == "section-1"
    assert result.nodes[2].text == "First paragraph."
    assert result.nodes[2].labels == ["source_chunk"]


def test_mock_adapter_creates_multiple_sections() -> None:
    result = MockIngestionAdapter().process(
        Path("sample.md"),
        "# One\n\nA\n\n## Two\n\nB",
        "md",
    )

    assert [node.node_key for node in result.nodes] == [
        "root",
        "section-1",
        "section-1-paragraph-1",
        "section-2",
        "section-2-paragraph-1",
    ]
