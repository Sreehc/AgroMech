from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from agromech_api.auth import create_access_token
from agromech_api.config import Settings
from agromech_api.db.enums import ChunkType, DocumentStatus, IngestTaskStatus, TaskType, UserRole
from agromech_api.db.models import document_chunks, documents, ingest_tasks, metadata
from agromech_api.main import create_app


def library_settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
    )


def library_client(tmp_path: Path, role: UserRole = UserRole.ADMIN) -> tuple[TestClient, object, str]:
    settings = library_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = create_access_token(username=role.value, role=role, settings=settings)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def seed_document(
    engine,
    *,
    document_id: str,
    task_id: str,
    brand: str = "Kubota",
    model: str = "M7040",
    document_type: str = "manual",
    language: str = "zh-CN",
    status: str = DocumentStatus.INDEXED.value,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id=document_id,
                title=f"{brand} {model}",
                original_file_name=f"{document_id}.txt",
                file_hash=f"hash-{document_id}",
                file_size_bytes=128,
                mime_type="text/plain",
                storage_uri=f"file:///tmp/{document_id}.txt",
                brand=brand,
                model=model,
                document_type=document_type,
                language=language,
                source="dealer",
                status=status,
                failure_stage="parse" if status == DocumentStatus.FAILED.value else None,
                failure_code="parse_failed" if status == DocumentStatus.FAILED.value else None,
                failure_message="Cannot parse file" if status == DocumentStatus.FAILED.value else None,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(ingest_tasks).values(
                id=task_id,
                document_id=document_id,
                task_type=TaskType.INGEST.value,
                status=IngestTaskStatus.SUCCEEDED.value,
                attempt_count=1,
                stage="indexed",
            )
        )
        connection.execute(
            insert(document_chunks).values(
                id=f"chunk-{document_id}",
                document_id=document_id,
                chunk_type=ChunkType.TEXT.value,
                content="Hydraulic maintenance interval and safety notes.",
                summary="Hydraulic maintenance interval",
                page_number=3,
                section_title="Maintenance",
                source_locator={"page": 3},
            )
        )


def test_document_list_supports_filters(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-a", task_id="task-a", brand="Kubota", model="M7040")
    seed_document(engine, document_id="doc-b", task_id="task-b", brand="Deere", model="5075E")

    response = client.get(
        "/documents?brand=Kubota&model=M7040&document_type=manual&language=zh-CN&status=indexed",
        headers=auth_header(token),
    )

    assert response.status_code == 200
    assert response.json()["total"] == 1
    assert response.json()["items"][0]["id"] == "doc-a"
    assert response.json()["items"][0]["brand"] == "Kubota"


def test_document_detail_returns_metadata_recent_task_failure_and_chunk_summary(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(
        engine,
        document_id="doc-failed",
        task_id="task-failed",
        status=DocumentStatus.FAILED.value,
    )

    response = client.get("/documents/doc-failed", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "doc-failed"
    assert payload["metadata"]["brand"] == "Kubota"
    assert payload["failure"]["code"] == "parse_failed"
    assert payload["recent_task"]["id"] == "task-failed"
    assert payload["chunks"][0] == {
        "id": "chunk-doc-failed",
        "chunk_type": "text",
        "summary": "Hydraulic maintenance interval",
        "page_number": 3,
        "section_title": "Maintenance",
    }


def test_task_query_returns_task_status(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.get("/tasks/task-a", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json()["id"] == "task-a"
    assert response.json()["document_id"] == "doc-a"
    assert response.json()["status"] == "succeeded"


def test_reprocess_creates_new_task(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.post("/documents/doc-a/reprocess", headers=auth_header(token))

    assert response.status_code == 201
    payload = response.json()
    assert payload["document_id"] == "doc-a"
    assert payload["task_id"]
    assert payload["status"] == "queued"

    with engine.connect() as connection:
        task_count = connection.execute(select(ingest_tasks)).all()
    assert len(task_count) == 2


def test_delete_marks_document_deleted(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.delete("/documents/doc-a", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json() == {"document_id": "doc-a", "status": "deleted"}

    with engine.connect() as connection:
        status = connection.execute(select(documents.c.status).where(documents.c.id == "doc-a")).scalar_one()
    assert status == "deleted"


def test_non_maintainer_write_returns_forbidden(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path, role=UserRole.USER)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.post("/documents/doc-a/reprocess", headers=auth_header(token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
