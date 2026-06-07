from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def sha256_file(path: Path | str) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def archive_source(path: Path | str, archive_dir: Path | str, checksum: str) -> Path:
    source_path = Path(path)
    destination_dir = Path(archive_dir) / checksum[:2]
    destination_dir.mkdir(parents=True, exist_ok=True)
    destination = destination_dir / f"{checksum}{source_path.suffix.lower()}"
    shutil.copy2(source_path, destination)
    return destination


def move_request_file(path: Path | str, destination_dir: Path | str, checksum: str) -> Path:
    source_path = Path(path)
    target_dir = Path(destination_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    destination = target_dir / f"{checksum}{source_path.suffix.lower()}"
    if destination.exists():
        destination = target_dir / f"{checksum}-{source_path.name}"
    return Path(shutil.move(str(source_path), str(destination)))
