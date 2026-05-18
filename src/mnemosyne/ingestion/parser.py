from __future__ import annotations

from pathlib import Path
import re


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}


def read_text_source(path: Path | str) -> tuple[str, str]:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported source type: {suffix or '<none>'}")
    text = source_path.read_text(encoding="utf-8-sig")
    kind = suffix.lstrip(".")
    if kind == "txt":
        text = prepare_plain_text_source(text)
    return text, kind


def prepare_plain_text_source(text: str) -> str:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    title = plain_text_title(normalized)
    normalized = strip_project_gutenberg_boilerplate(normalized)
    if title and not starts_with_markdown_heading(normalized):
        normalized = f"# {title}\n\n{normalized.lstrip()}"
    return normalized


def plain_text_title(text: str) -> str | None:
    for line in text.splitlines()[:80]:
        match = re.match(r"\s*Title:\s*(.+?)\s*$", line)
        if match:
            return match.group(1).strip() or None
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    gutenberg_prefix = "The Project Gutenberg eBook of "
    if first_line.startswith(gutenberg_prefix):
        return first_line.removeprefix(gutenberg_prefix).strip() or None
    return None


def strip_project_gutenberg_boilerplate(text: str) -> str:
    lines = text.splitlines()
    start_index = None
    end_index = None
    for index, line in enumerate(lines):
        if line.startswith("*** START OF THE PROJECT GUTENBERG EBOOK"):
            start_index = index + 1
            break
    for index, line in enumerate(lines):
        if line.startswith("*** END OF THE PROJECT GUTENBERG EBOOK"):
            end_index = index
            break
    if start_index is None and end_index is None:
        return text.strip()
    body = lines[start_index or 0 : end_index]
    return "\n".join(body).strip()


def starts_with_markdown_heading(text: str) -> bool:
    first_line = next((line.strip() for line in text.splitlines() if line.strip()), "")
    return first_line.startswith("#")
