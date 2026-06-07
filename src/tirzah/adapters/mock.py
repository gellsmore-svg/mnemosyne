from __future__ import annotations

from pathlib import Path

from tirzah.models.ingestion import IngestedNode, IngestionResult, SourceRef


class MockIngestionAdapter:
    def process(
        self,
        path: Path,
        text: str,
        source_kind: str,
        extra_labels: list[str] | None = None,
    ) -> IngestionResult:
        title = first_heading(text) or path.stem
        sections = parse_sections(text, title)
        labels = normalized_extra_labels(extra_labels)
        nodes = [with_extra_labels(root_node(title, text, len(sections)), labels)]
        for section_index, section in enumerate(sections, start=1):
            section_key = f"section-{section_index}"
            nodes.append(
                with_extra_labels(
                    IngestedNode(
                        node_key=section_key,
                        parent_key="root",
                        title=section["title"],
                        text=section["text"],
                        labels=["source_section"],
                        metadata={
                            "adapter": "mock",
                            "chunk_strategy": "markdown_sections",
                            "section_index": section_index,
                        },
                    ),
                    labels,
                )
            )
            for paragraph_index, paragraph in enumerate(section["paragraphs"], start=1):
                nodes.append(
                    with_extra_labels(
                        IngestedNode(
                            node_key=f"{section_key}-paragraph-{paragraph_index}",
                            parent_key=section_key,
                            title=f"{section['title']} / paragraph {paragraph_index}",
                            text=paragraph,
                            labels=["source_chunk"],
                            metadata={
                                "adapter": "mock",
                                "chunk_strategy": "markdown_paragraphs",
                                "section_index": section_index,
                                "paragraph_index": paragraph_index,
                            },
                        ),
                        labels,
                    )
                )
        return IngestionResult(
            source=SourceRef(path=str(path), kind=source_kind),
            title=title,
            summary=summarize(text),
            nodes=nodes,
            adapter="mock",
        )


def first_heading(text: str) -> str | None:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def root_node(title: str, text: str, section_count: int) -> IngestedNode:
    return IngestedNode(
        node_key="root",
        title=title,
        text=summarize(text),
        labels=["source_root"],
        metadata={
            "adapter": "mock",
            "chunk_strategy": "document_root",
            "section_count": section_count,
        },
    )


def normalized_extra_labels(labels: list[str] | None) -> list[str]:
    normalized = []
    for label in labels or []:
        cleaned = "_".join(label.strip().lower().split())
        if cleaned and cleaned not in normalized:
            normalized.append(cleaned)
    return normalized


def with_extra_labels(node: IngestedNode, extra_labels: list[str]) -> IngestedNode:
    for label in extra_labels:
        if label not in node.labels:
            node.labels.append(label)
    return node


def parse_sections(text: str, fallback_title: str) -> list[dict]:
    sections: list[dict] = []
    current_title = fallback_title
    current_lines: list[str] = []

    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            if current_lines:
                sections.append(section(current_title, "\n".join(current_lines)))
            current_title = stripped.lstrip("#").strip() or fallback_title
            current_lines = []
        else:
            current_lines.append(line)

    if current_lines:
        sections.append(section(current_title, "\n".join(current_lines)))
    if not sections:
        sections.append(section(fallback_title, text))
    return sections


def section(title: str, text: str) -> dict:
    paragraphs = [block.strip() for block in text.split("\n\n") if block.strip()]
    section_text = text.strip()
    return {"title": title, "text": section_text, "paragraphs": paragraphs or [section_text]}


def summarize(text: str, limit: int = 500) -> str:
    compact = " ".join(text.split())
    return compact[:limit]
