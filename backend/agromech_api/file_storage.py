from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class StoredFile:
    uri: str
    path: Path


class LocalFileStorage:
    def __init__(self, root_path: str) -> None:
        self.root_path = Path(root_path)

    def save(self, *, file_hash: str, original_name: str, content: bytes) -> StoredFile:
        extension = Path(original_name).suffix.lower()
        safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(original_name).stem).strip("-") or "upload"
        directory = self.root_path / file_hash[:2] / file_hash[2:4]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{file_hash}-{safe_stem}{extension}"
        path.write_bytes(content)
        return StoredFile(uri=f"file://{path.resolve()}", path=path)
