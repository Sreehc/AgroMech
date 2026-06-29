from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from agromech_api.auth import create_database_user
from agromech_api.config import Settings
from agromech_api.db.enums import DocumentStatus, UserRole
from agromech_api.db.models import documents, metadata
from agromech_api.main import create_app
from agromech_worker.main import run_once
from test_hybrid_retrieval import seed_retrieval_corpus


def e2e_settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        file_storage_backend="local",
        local_file_storage_path=str(tmp_path / "files"),
        upload_max_file_size_mb=1,
        upload_max_image_size_mb=1,
        graph_backend="local",
        vector_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
    )


def test_e2e_login_upload_process_question_and_trace(tmp_path: Path) -> None:
    settings = e2e_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    create_database_user(engine, username="admin", password="secret", role=UserRole.ADMIN)
    client = TestClient(create_app(settings=settings, database_engine=engine))

    login = client.post("/auth/login", json={"username": "admin", "password": "secret"})
    assert login.status_code == 200
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    upload = client.post(
        "/documents",
        headers=headers,
        data={"brand": "Kubota", "model": "M7040", "document_type": "manual", "language": "zh-CN"},
        files={"file": ("manual.txt", b"Kubota M7040 hydraulic pump fault code E01.", "text/plain")},
    )
    assert upload.status_code == 201
    document_id = upload.json()["document_id"]

    assert run_once(engine=engine, processor=lambda _task: None) == "succeeded"
    with engine.connect() as connection:
        uploaded_status = connection.execute(
            select(documents.c.status).where(documents.c.id == document_id)
        ).scalar_one()
    assert uploaded_status == DocumentStatus.INDEXED.value

    seed_retrieval_corpus(engine)
    answer = client.post(
        "/qa/text",
        headers={**headers, "X-Trace-Id": "e2e-trace"},
        json={"question": "M7040 E01 hydraulic pump repair", "filters": {"model": "M7040"}},
    )
    assert answer.status_code == 200
    assert answer.json()["citations"]
    assert answer.json()["trace_id"] == "e2e-trace"

    trace = client.get("/retrieval-traces/e2e-trace", headers=headers)
    assert trace.status_code == 200
    assert trace.json()["trace_id"] == "e2e-trace"
