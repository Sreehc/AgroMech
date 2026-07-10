from pathlib import Path

import pytest

from agromech_api.core.config import Settings
from agromech_api.integrations.storage.file_storage import (
    LocalFileStorage,
    OSSFileStorage,
    build_file_storage,
    oss_key_from_uri,
)


def oss_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "oss",
        "graph_backend": "local",
        "model_provider": "local",
        "embedding_provider": "local",
        "oss_region": "cn-beijing",
        "oss_endpoint": "https://oss-cn-beijing.aliyuncs.com",
        "oss_bucket": "agromech-test",
        "oss_access_key_id": "id",
        "oss_access_key_secret": "secret",
        "oss_prefix": "agromech/dev",
        "oss_signed_url_ttl_seconds": 600,
        "oss_download_signed_url_ttl_seconds": 3600,
    }
    base.update(overrides)
    return Settings(**base)


class FakeBucket:
    """In-memory stand-in for oss2.Bucket so tests stay offline."""

    def __init__(self) -> None:
        self.objects: dict[str, bytes] = {}
        self.signed: list[tuple[str, str, int]] = []

    def put_object(self, key: str, content: bytes) -> None:
        self.objects[key] = content

    def get_object(self, key: str):
        data = self.objects[key]

        class _Result:
            def read(self_inner) -> bytes:
                return data

        return _Result()

    def sign_url(self, method: str, key: str, ttl: int) -> str:
        self.signed.append((method, key, ttl))
        return f"https://signed.example/{key}?expires={ttl}"


def test_local_storage_save_and_read_roundtrip(tmp_path: Path) -> None:
    storage = LocalFileStorage(str(tmp_path / "files"))

    stored = storage.save(file_hash="a" * 64, original_name="My Manual.pdf", content=b"hello")

    assert stored.uri.startswith("file://")
    assert stored.path is not None and stored.path.exists()
    assert storage.read(stored.uri) == b"hello"
    assert storage.signed_url(stored.uri) is None


def test_build_file_storage_selects_local_by_default() -> None:
    settings = Settings(
        _env_file=None,
        file_storage_backend="local",
        graph_backend="local",
        model_provider="local",
        embedding_provider="local",
    )

    storage = build_file_storage(settings)

    assert isinstance(storage, LocalFileStorage)


def test_build_file_storage_selects_oss_backend() -> None:
    storage = build_file_storage(oss_settings(), oss_bucket=FakeBucket())

    assert isinstance(storage, OSSFileStorage)


def test_oss_storage_save_uses_stable_object_key_and_uri() -> None:
    bucket = FakeBucket()
    storage = OSSFileStorage(oss_settings(), bucket=bucket)

    stored = storage.save(file_hash="b" * 64, original_name="Repair Guide.pdf", content=b"data")

    expected_key = f"agromech/dev/documents/{'b' * 64}/original/{'b' * 64}-Repair-Guide.pdf"
    assert stored.uri == f"oss://agromech-test/{expected_key}"
    assert stored.path is None
    assert bucket.objects[expected_key] == b"data"


def test_oss_storage_read_roundtrip() -> None:
    bucket = FakeBucket()
    storage = OSSFileStorage(oss_settings(), bucket=bucket)
    stored = storage.save(file_hash="c" * 64, original_name="x.txt", content=b"payload")

    assert storage.read(stored.uri) == b"payload"


def test_oss_preview_signed_url_uses_600_second_ttl() -> None:
    bucket = FakeBucket()
    storage = OSSFileStorage(oss_settings(), bucket=bucket)
    stored = storage.save(file_hash="d" * 64, original_name="x.txt", content=b"payload")

    url = storage.signed_url(stored.uri)

    assert url is not None
    assert bucket.signed[-1][2] == 600


def test_oss_download_signed_url_uses_3600_second_ttl() -> None:
    bucket = FakeBucket()
    storage = OSSFileStorage(oss_settings(), bucket=bucket)
    stored = storage.save(file_hash="e" * 64, original_name="x.txt", content=b"payload")

    storage.signed_url(stored.uri, download=True)

    assert bucket.signed[-1][2] == 3600


def test_oss_key_from_uri_rejects_mismatched_bucket() -> None:
    with pytest.raises(ValueError, match="does not match configured bucket"):
        oss_key_from_uri("oss://other-bucket/agromech/dev/x", "agromech-test")


def test_oss_key_from_uri_rejects_non_oss_scheme() -> None:
    with pytest.raises(ValueError, match="Not an OSS URI"):
        oss_key_from_uri("file:///tmp/x", "agromech-test")
