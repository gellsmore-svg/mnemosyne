from __future__ import annotations

from pathlib import Path


SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}


def read_text_source(path: Path | str) -> tuple[str, str]:
    source_path = Path(path)
    suffix = source_path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported source type: {suffix or '<none>'}")
    return source_path.read_text(encoding="utf-8"), suffix.lstrip(".")
