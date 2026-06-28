from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from agromech_api.auth import create_access_token
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import documents, ingest_tasks, metadata
from agromech_api.main import create_app
from agromech_api.task_queue import InMemoryTaskPublisher


def upload_settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        file_storage_backend="local",
        local_file_storage_path=str(tmp_path / "files"),
        upload_max_file_size_mb=1,
        upload_max_image_size_mb=1,
    )


def upload_client(
    tmp_path: Path,
    *,
    task_publisher: InMemoryTaskPublisher | None = None,
) -> tuple[TestClient, object, str]:
    settings = upload_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = create_access_token(username="admin", role=UserRole.ADMIN, settings=settings)
    return TestClient(
        create_app(settings=settings, database_engine=engine, task_publisher=task_publisher)
    ), engine, token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_upload_creates_document_task_and_stores_file(tmp_path: Path) -> None:
    client, engine, token = upload_client(tmp_path)

    response = client.post(
        "/documents",
        headers=auth_header(token),
        data={
            "brand": "Kubota",
            "model": "M7040",
            "document_type": "manual",
            "language": "zh-CN",
            "source": "dealer",
        },
        files={"file": ("manual.txt", b"maintenance schedule", "text/plain")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert payload["status"] == "queued"
    assert payload["document_id"]
    assert payload["task_id"]
    assert payload["duplicate_of"] is None

    with engine.connect() as connection:
        document = connection.execute(select(documents)).mappings().one()
        task = connection.execute(select(ingest_tasks)).mappings().one()

    assert document["id"] == payload["document_id"]
    assert document["brand"] == "Kubota"
    assert document["model"] == "M7040"
    assert document["status"] == "queued"
    assert Path(document["storage_uri"].replace("file://", "")).exists()
    assert task["id"] == payload["task_id"]
    assert task["document_id"] == payload["document_id"]
    assert task["task_type"] == "ingest"
    assert task["status"] == "queued"


def test_upload_publishes_ingest_task_message(tmp_path: Path) -> None:
    publisher = InMemoryTaskPublisher()
    client, _engine, token = upload_client(tmp_path, task_publisher=publisher)

    response = client.post(
        "/documents",
        headers=auth_header(token),
        files={"file": ("manual.txt", b"hydraulic pump service", "text/plain")},
    )

    assert response.status_code == 201
    payload = response.json()
    assert len(publisher.messages) == 1
    assert publisher.messages[0].task_id == payload["task_id"]
    assert publisher.messages[0].document_id == payload["document_id"]
    assert publisher.messages[0].task_type == "ingest"


def test_upload_accepts_all_t09_supported_file_types(tmp_path: Path) -> None:
    client, _engine, token = upload_client(tmp_path)

    supported_cases = [
        ("manual.pdf", b"%PDF-1.4", "application/pdf"),
        ("manual.docx", b"docx", "application/vnd.openxmlformats-officedocument.wordprocessingml.document"),
        ("manual.xlsx", b"xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"),
        ("manual.csv", b"a,b\n1,2\n", "text/csv"),
        ("manual.txt", b"text", "text/plain"),
        ("manual.md", b"# title", "text/markdown"),
        ("photo.png", b"png", "image/png"),
    ]

    for filename, content, media_type in supported_cases:
        response = client.post(
            "/documents",
            headers=auth_header(token),
            files={"file": (filename, content, media_type)},
        )

        assert response.status_code == 201, filename
        payload = response.json()
        assert payload["status"] == "queued"
        assert payload["document_id"]
        assert payload["task_id"]
        assert payload["duplicate_of"] is None


def test_upload_rejects_unsupported_file_type(tmp_path: Path) -> None:
    client, _engine, token = upload_client(tmp_path)

    response = client.post(
        "/documents",
        headers=auth_header(token),
        files={"file": ("payload.exe", b"binary", "application/octet-stream")},
    )

    assert response.status_code == 415
    assert response.json()["error"]["code"] == "unsupported_file_type"


def test_upload_rejects_files_over_configured_limit(tmp_path: Path) -> None:
    client, _engine, token = upload_client(tmp_path)

    response = client.post(
        "/documents",
        headers=auth_header(token),
        files={"file": ("manual.txt", b"x" * (1024 * 1024 + 1), "text/plain")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"


def test_upload_uses_image_size_limit_for_supported_images(tmp_path: Path) -> None:
    settings = Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        file_storage_backend="local",
        local_file_storage_path=str(tmp_path / "files"),
        upload_max_file_size_mb=2,
        upload_max_image_size_mb=0,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech-images.db'}")
    metadata.create_all(engine)
    token = create_access_token(username="admin", role=UserRole.ADMIN, settings=settings)
    client = TestClient(create_app(settings=settings, database_engine=engine))

    response = client.post(
        "/documents",
        headers=auth_header(token),
        files={"file": ("photo.png", b"x", "image/png")},
    )

    assert response.status_code == 413
    assert response.json()["error"]["code"] == "file_too_large"
    assert response.json()["error"]["details"] == {"limit_bytes": 0}


def test_upload_does_not_apply_image_limit_to_non_image_files(tmp_path: Path) -> None:
    settings = Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        file_storage_backend="local",
        local_file_storage_path=str(tmp_path / "files"),
        upload_max_file_size_mb=1,
        upload_max_image_size_mb=0,
    )
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech-docs.db'}")
    metadata.create_all(engine)
    token = create_access_token(username="admin", role=UserRole.ADMIN, settings=settings)
    client = TestClient(create_app(settings=settings, database_engine=engine))

    response = client.post(
        "/documents",
        headers=auth_header(token),
        files={"file": ("manual.txt", b"text", "text/plain")},
    )

    assert response.status_code == 201
    assert response.json()["status"] == "queued"


def test_upload_duplicate_returns_duplicate_of(tmp_path: Path) -> None:
    client, _engine, token = upload_client(tmp_path)
    file_payload = {"file": ("manual.txt", b"same content", "text/plain")}

    first = client.post("/documents", headers=auth_header(token), files=file_payload)
    second = client.post("/documents", headers=auth_header(token), files=file_payload)

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "duplicate_of"
    assert second.json()["error"]["details"] == {"document_id": first.json()["document_id"]}


def test_upload_requires_login(tmp_path: Path) -> None:
    client, _engine, _token = upload_client(tmp_path)

    response = client.post(
        "/documents",
        files={"file": ("manual.txt", b"maintenance schedule", "text/plain")},
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
