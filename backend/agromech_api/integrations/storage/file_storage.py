from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import urlsplit

from agromech_api.core.config import Settings


@dataclass(frozen=True)
class StoredFile:
    uri: str
    # Local backends expose an on-disk path; remote backends (OSS) leave it None.
    path: Path | None = None


class FileStorage(Protocol):
    def save(self, *, file_hash: str, original_name: str, content: bytes) -> StoredFile: ...

    def read(self, uri: str) -> bytes: ...

    def signed_url(self, uri: str, *, download: bool = False) -> str | None: ...


def object_name(file_hash: str, original_name: str) -> str:
    """Stable, sanitized object/file name shared by every backend."""
    extension = Path(original_name).suffix.lower()
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", Path(original_name).stem).strip("-") or "upload"
    return f"{file_hash}-{safe_stem}{extension}"


class LocalFileStorage:
    backend = "local"

    def __init__(self, root_path: str) -> None:
        self.root_path = Path(root_path)

    def save(self, *, file_hash: str, original_name: str, content: bytes) -> StoredFile:
        directory = self.root_path / file_hash[:2] / file_hash[2:4]
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / object_name(file_hash, original_name)
        path.write_bytes(content)
        return StoredFile(uri=f"file://{path.resolve()}", path=path)

    def read(self, uri: str) -> bytes:
        return Path(local_path_from_uri(uri)).read_bytes()

    def signed_url(self, uri: str, *, download: bool = False) -> str | None:
        # Local files are served directly by the API, not via signed URLs.
        return None


class OSSFileStorage:
    """Aliyun OSS backend.

    The bucket is created lazily so importing this module never requires the
    `oss2` SDK; a readable error is raised only when an OSS-backed operation is
    actually attempted. A `bucket` can be injected for offline testing.
    """

    backend = "oss"

    def __init__(self, settings: Settings, *, bucket=None) -> None:
        self.region = settings.oss_region
        self.endpoint = settings.oss_endpoint
        self.bucket_name = settings.oss_bucket
        self.access_key_id = settings.oss_access_key_id
        self.access_key_secret = settings.oss_access_key_secret
        self.prefix = settings.oss_prefix.strip("/")
        self.preview_ttl = settings.oss_signed_url_ttl_seconds
        self.download_ttl = settings.oss_download_signed_url_ttl_seconds
        self._bucket = bucket

    @property
    def bucket(self):
        if self._bucket is None:
            self._bucket = self._build_bucket()
        return self._bucket

    def _build_bucket(self):
        try:
            import oss2
        except ImportError as exc:  # pragma: no cover - exercised only without the SDK
            raise RuntimeError(
                "FILE_STORAGE_BACKEND=oss requires the 'oss2' package. "
                "Install project dependencies with: pip install -e ."
            ) from exc
        auth = oss2.Auth(self.access_key_id, self.access_key_secret)
        return oss2.Bucket(auth, self.endpoint, self.bucket_name)

    def object_key(self, file_hash: str, original_name: str) -> str:
        name = object_name(file_hash, original_name)
        parts = [part for part in (self.prefix, "documents", file_hash, "original", name) if part]
        return "/".join(parts)

    def save(self, *, file_hash: str, original_name: str, content: bytes) -> StoredFile:
        key = self.object_key(file_hash, original_name)
        self.bucket.put_object(key, content)
        return StoredFile(uri=f"oss://{self.bucket_name}/{key}", path=None)

    def read(self, uri: str) -> bytes:
        key = oss_key_from_uri(uri, self.bucket_name)
        return self.bucket.get_object(key).read()

    def signed_url(self, uri: str, *, download: bool = False) -> str | None:
        key = oss_key_from_uri(uri, self.bucket_name)
        ttl = self.download_ttl if download else self.preview_ttl
        return self.bucket.sign_url("GET", key, ttl)


def build_file_storage(settings: Settings, *, oss_bucket=None) -> FileStorage:
    if settings.file_storage_backend == "oss":
        return OSSFileStorage(settings, bucket=oss_bucket)
    return LocalFileStorage(settings.local_file_storage_path)


def local_path_from_uri(uri: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme != "file":
        raise ValueError(f"Not a local file URI: {uri}")
    return parsed.path


def oss_key_from_uri(uri: str, bucket_name: str) -> str:
    parsed = urlsplit(uri)
    if parsed.scheme != "oss":
        raise ValueError(f"Not an OSS URI: {uri}")
    if parsed.netloc != bucket_name:
        raise ValueError(f"OSS URI bucket {parsed.netloc!r} does not match configured bucket {bucket_name!r}")
    return parsed.path.lstrip("/")
