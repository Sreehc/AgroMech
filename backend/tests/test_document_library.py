from pathlib import Path
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from auth_helpers import auth_token_for_user
from agromech_api.config import Settings
from agromech_api.db.enums import AssetType, ChunkType, DocumentStatus, IngestTaskStatus, TaskType, UserRole
from agromech_api.db.models import answer_citations, chunk_search_index, document_assets, document_chunks, documents, ingest_tasks, metadata, qa_records
from agromech_api.main import create_app
from agromech_api.task_queue import InMemoryTaskPublisher


def library_settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
    )


def library_client(
    tmp_path: Path,
    role: UserRole = UserRole.ADMIN,
    *,
    task_publisher: InMemoryTaskPublisher | None = None,
) -> tuple[TestClient, object, str]:
    settings = library_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = auth_token_for_user(engine, settings, username=role.value, role=role)
    return TestClient(
        create_app(settings=settings, database_engine=engine, task_publisher=task_publisher)
    ), engine, token


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
    mime_type: str = "text/plain",
    chunk_page_number: int | None = 3,
    chunk_source_locator: dict[str, object] | None = None,
) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id=document_id,
                title=f"{brand} {model}",
                original_file_name=f"{document_id}.pdf" if mime_type == "application/pdf" else f"{document_id}.txt",
                file_hash=f"hash-{document_id}",
                file_size_bytes=128,
                mime_type=mime_type,
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
                page_number=chunk_page_number,
                section_title="Maintenance",
                source_locator=chunk_source_locator
                if chunk_source_locator is not None
                else ({"page": chunk_page_number} if chunk_page_number is not None else {"type": "pdf"}),
            )
        )


def seed_pdf_page_asset(
    engine,
    *,
    document_id: str,
    asset_id: str,
    page_number: int,
    path: Path,
) -> None:
    path.write_bytes(b"page-image")
    with engine.begin() as connection:
        connection.execute(
            insert(document_assets).values(
                id=asset_id,
                document_id=document_id,
                asset_type=AssetType.PAGE_IMAGE.value,
                storage_uri=f"file://{path}",
                mime_type="image/png",
                page_number=page_number,
                source_locator={"type": "pdf_page", "page": page_number},
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


def test_document_list_returns_summary_recent_task_failure_and_updated_at(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-indexed", task_id="task-indexed", brand="Kubota", model="M7040")
    seed_document(
        engine,
        document_id="doc-failed",
        task_id="task-failed-old",
        brand="Deere",
        model="5075E",
        status=DocumentStatus.FAILED.value,
    )

    now = datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(ingest_tasks).values(
                id="task-failed-latest",
                document_id="doc-failed",
                task_type=TaskType.REPROCESS.value,
                status=IngestTaskStatus.FAILED.value,
                attempt_count=2,
                stage="ocr",
                error_code="ocr_failed",
                error_message="OCR service failed",
                created_at=now + timedelta(seconds=1),
                updated_at=now + timedelta(seconds=1),
            )
        )

    response = client.get("/documents", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 2

    indexed_item = next(item for item in payload["items"] if item["id"] == "doc-indexed")
    assert indexed_item["summary"] == "Hydraulic maintenance interval"
    assert indexed_item["recent_task"] == {
        "id": "task-indexed",
        "task_type": "ingest",
        "status": "succeeded",
        "stage": "indexed",
    }
    assert indexed_item["failure"] == {
        "stage": None,
        "code": None,
        "message": None,
    }
    assert indexed_item["updated_at"] is not None

    failed_item = next(item for item in payload["items"] if item["id"] == "doc-failed")
    assert failed_item["summary"] == "Hydraulic maintenance interval"
    assert failed_item["recent_task"] == {
        "id": "task-failed-latest",
        "task_type": "reprocess",
        "status": "failed",
        "stage": "ocr",
    }
    assert failed_item["failure"] == {
        "stage": "parse",
        "code": "parse_failed",
        "message": "Cannot parse file",
    }
    assert failed_item["updated_at"] is not None


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
    assert payload["status"] == "failed"
    assert payload["accessible"] is True
    assert payload["updated_at"] is not None
    assert payload["metadata"]["brand"] == "Kubota"
    assert payload["failure"]["code"] == "parse_failed"
    assert payload["recent_task"]["id"] == "task-failed"
    assert payload["chunks"][0] == {
        "id": "chunk-doc-failed",
        "chunk_type": "text",
        "summary": "Hydraulic maintenance interval",
        "page_number": 3,
        "section_title": "Maintenance",
        "source_locator": {"page": 3},
        "source_position": {
            "page_number": 3,
            "section_title": "Maintenance",
            "worksheet_name": None,
            "row_start": None,
            "row_end": None,
        },
        "accessible": True,
    }


def test_document_detail_returns_deleted_document_as_inaccessible_without_blank_payload(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(
        engine,
        document_id="doc-deleted",
        task_id="task-deleted",
        status=DocumentStatus.DELETED.value,
    )

    response = client.get("/documents/doc-deleted", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["id"] == "doc-deleted"
    assert payload["status"] == "deleted"
    assert payload["accessible"] is False
    assert payload["metadata"]["original_file_name"] == "doc-deleted.txt"
    assert payload["chunks"][0]["accessible"] is False
    assert payload["chunks"][0]["source_locator"] == {"page": 3}


def test_document_preview_returns_text_contract_for_chunk(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.get("/documents/doc-a/preview?chunk_id=chunk-doc-a", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json() == {
        "document_id": "doc-a",
        "document_title": "Kubota M7040",
        "chunk_id": "chunk-doc-a",
        "preview_type": "text",
        "accessible": True,
        "source_locator": {"page": 3},
        "source_position": {
            "page_number": 3,
            "section_title": "Maintenance",
            "worksheet_name": None,
            "row_start": None,
            "row_end": None,
        },
        "evidence_snippet": "Hydraulic maintenance interval and safety notes.",
        "text_preview": "Hydraulic maintenance interval and safety notes.",
        "pdf_page": None,
        "highlights": [
            {
                "type": "text",
                "text": "Hydraulic maintenance interval and safety notes.",
                "page_number": 3,
                "source_locator": {"page": 3},
            }
        ],
        "unavailable_reason": None,
    }


def test_document_preview_returns_pdf_contract_without_rendered_page(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-pdf", task_id="task-pdf", mime_type="application/pdf")

    response = client.get("/documents/doc-pdf/preview?chunk_id=chunk-doc-pdf", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["preview_type"] == "pdf"
    assert payload["accessible"] is True
    assert payload["source_position"]["page_number"] == 3
    assert payload["pdf_page"] == {
        "page_number": 3,
        "page_image_url": None,
        "render_status": "not_rendered",
    }
    assert payload["highlights"][0]["type"] == "text"


def test_document_preview_returns_rendered_pdf_page_resource_and_highlight(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-pdf", task_id="task-pdf", mime_type="application/pdf")
    seed_pdf_page_asset(
        engine,
        document_id="doc-pdf",
        asset_id="asset-page-3",
        page_number=3,
        path=tmp_path / "page-3.png",
    )

    response = client.get("/documents/doc-pdf/preview?chunk_id=chunk-doc-pdf", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["pdf_page"] == {
        "page_number": 3,
        "page_image_url": "/documents/doc-pdf/assets/asset-page-3",
        "render_status": "rendered",
    }
    assert payload["highlights"] == [
        {
            "type": "text",
            "text": "Hydraulic maintenance interval and safety notes.",
            "page_number": 3,
            "source_locator": {"page": 3},
        }
    ]

    asset_response = client.get(payload["pdf_page"]["page_image_url"], headers=auth_header(token))
    assert asset_response.status_code == 200
    assert asset_response.content == b"page-image"


def test_document_preview_returns_pdf_area_highlight_from_source_locator_bbox(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(
        engine,
        document_id="doc-pdf",
        task_id="task-pdf",
        mime_type="application/pdf",
        chunk_source_locator={
            "type": "pdf",
            "page": 3,
            "bbox": {"x": 0.12, "y": 0.24, "width": 0.5, "height": 0.16},
        },
    )
    seed_pdf_page_asset(
        engine,
        document_id="doc-pdf",
        asset_id="asset-page-3",
        page_number=3,
        path=tmp_path / "page-3.png",
    )

    response = client.get("/documents/doc-pdf/preview?chunk_id=chunk-doc-pdf", headers=auth_header(token))

    assert response.status_code == 200
    area_highlights = [highlight for highlight in response.json()["highlights"] if highlight["type"] == "area"]
    assert area_highlights == [
        {
            "type": "area",
            "page_number": 3,
            "source_locator": {
                "type": "pdf",
                "page": 3,
                "bbox": {"x": 0.12, "y": 0.24, "width": 0.5, "height": 0.16},
            },
            "bbox": {"x": 0.12, "y": 0.24, "width": 0.5, "height": 0.16},
        }
    ]


def test_document_preview_reports_missing_pdf_page_locator(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(
        engine,
        document_id="doc-pdf",
        task_id="task-pdf",
        mime_type="application/pdf",
        chunk_page_number=None,
    )

    response = client.get("/documents/doc-pdf/preview?chunk_id=chunk-doc-pdf", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["preview_type"] == "unavailable"
    assert payload["accessible"] is False
    assert payload["pdf_page"] is None
    assert payload["highlights"] == []
    assert payload["unavailable_reason"] == "pdf_page_locator_missing"


def test_document_preview_reports_missing_pdf_page_file(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-pdf", task_id="task-pdf", mime_type="application/pdf")
    with engine.begin() as connection:
        connection.execute(
            insert(document_assets).values(
                id="asset-missing",
                document_id="doc-pdf",
                asset_type=AssetType.PAGE_IMAGE.value,
                storage_uri=f"file://{tmp_path / 'missing-page.png'}",
                mime_type="image/png",
                page_number=3,
                source_locator={"type": "pdf_page", "page": 3},
            )
        )

    response = client.get("/documents/doc-pdf/preview?chunk_id=chunk-doc-pdf", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["preview_type"] == "unavailable"
    assert payload["accessible"] is False
    assert payload["pdf_page"] is None
    assert payload["highlights"] == []
    assert payload["unavailable_reason"] == "pdf_page_file_missing"


def test_document_preview_returns_inaccessible_contract_for_deleted_document(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-deleted", task_id="task-deleted", status=DocumentStatus.DELETED.value)

    response = client.get("/documents/doc-deleted/preview?chunk_id=chunk-doc-deleted", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json() == {
        "document_id": "doc-deleted",
        "document_title": "Kubota M7040",
        "chunk_id": "chunk-doc-deleted",
        "preview_type": "unavailable",
        "accessible": False,
        "source_locator": {"page": 3},
        "source_position": {
            "page_number": 3,
            "section_title": "Maintenance",
            "worksheet_name": None,
            "row_start": None,
            "row_end": None,
        },
        "evidence_snippet": "Hydraulic maintenance interval and safety notes.",
        "text_preview": None,
        "pdf_page": None,
        "highlights": [],
        "unavailable_reason": "document_deleted",
    }


def test_deleted_document_historical_citation_keeps_metadata_and_preview_stays_unavailable(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-deleted", task_id="task-deleted", status=DocumentStatus.DELETED.value)
    with engine.begin() as connection:
        connection.execute(
            insert(qa_records).values(
                id="qa-1",
                trace_id="trace-historical-citation",
                question="question",
                answer="answer",
                uncertainty={"level": "low", "reasons": []},
            )
        )
        connection.execute(
            insert(answer_citations).values(
                id="citation-1",
                qa_record_id="qa-1",
                document_id=None,
                chunk_id=None,
                citation_payload={
                    "document_id": "doc-deleted",
                    "document_title": "Kubota M7040",
                    "chunk_id": "chunk-doc-deleted",
                    "source_locator": {"page": 3},
                    "evidence_snippet": "Hydraulic maintenance interval and safety notes.",
                    "evidence_type": "text",
                    "accessible": False,
                },
                accessible=False,
            )
        )

    response = client.get("/documents/doc-deleted/preview?chunk_id=chunk-doc-deleted", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["accessible"] is False
    assert payload["document_title"] == "Kubota M7040"
    assert payload["source_locator"] == {"page": 3}
    assert payload["evidence_snippet"] == "Hydraulic maintenance interval and safety notes."
    assert payload["unavailable_reason"] == "document_deleted"


def test_task_query_returns_task_status(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.get("/tasks/task-a", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json()["id"] == "task-a"
    assert response.json()["document_id"] == "doc-a"
    assert response.json()["status"] == "succeeded"


def test_reprocess_creates_new_task(tmp_path: Path) -> None:
    publisher = InMemoryTaskPublisher()
    client, engine, token = library_client(tmp_path, task_publisher=publisher)
    seed_document(engine, document_id="doc-a", task_id="task-a")
    with engine.begin() as connection:
        connection.execute(
            insert(chunk_search_index).values(
                id="search-doc-a",
                chunk_id="chunk-doc-a",
                document_id="doc-a",
                chunk_type=ChunkType.TEXT.value,
                search_text="Hydraulic maintenance interval and safety notes.",
                embedding=[1.0],
            )
        )

    response = client.post("/documents/doc-a/reprocess", headers=auth_header(token))

    assert response.status_code == 201
    payload = response.json()
    assert payload["document_id"] == "doc-a"
    assert payload["task_id"]
    assert payload["status"] == "queued"
    assert len(publisher.messages) == 1
    assert publisher.messages[0].task_id == payload["task_id"]
    assert publisher.messages[0].document_id == "doc-a"
    assert publisher.messages[0].task_type == "reprocess"

    with engine.connect() as connection:
        task_count = connection.execute(select(ingest_tasks)).all()
        document = connection.execute(select(documents).where(documents.c.id == "doc-a")).mappings().one()
    assert len(task_count) == 2
    assert document["status"] == "indexed"


def test_reprocess_failed_document_without_old_index_marks_reprocessing_and_keeps_failure_visible(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-failed", task_id="task-failed", status=DocumentStatus.FAILED.value)

    response = client.post("/documents/doc-failed/reprocess", headers=auth_header(token))

    assert response.status_code == 201
    with engine.connect() as connection:
        document = connection.execute(select(documents).where(documents.c.id == "doc-failed")).mappings().one()
        tasks = connection.execute(select(ingest_tasks).where(ingest_tasks.c.document_id == "doc-failed")).mappings().all()
    assert document["status"] == "reprocessing"
    assert document["failure_code"] == "parse_failed"
    assert len(tasks) == 2


def test_reprocess_rejects_deleted_or_already_reprocessing_documents(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-deleted", task_id="task-deleted", status=DocumentStatus.DELETED.value)
    seed_document(engine, document_id="doc-reprocessing", task_id="task-reprocessing", status=DocumentStatus.REPROCESSING.value)

    deleted_response = client.post("/documents/doc-deleted/reprocess", headers=auth_header(token))
    reprocessing_response = client.post("/documents/doc-reprocessing/reprocess", headers=auth_header(token))

    assert deleted_response.status_code == 409
    assert deleted_response.json()["error"]["code"] == "validation_error"
    assert reprocessing_response.status_code == 409
    assert reprocessing_response.json()["error"]["code"] == "validation_error"


def test_delete_marks_document_deleted(tmp_path: Path) -> None:
    publisher = InMemoryTaskPublisher()
    client, engine, token = library_client(tmp_path, task_publisher=publisher)
    seed_document(engine, document_id="doc-a", task_id="task-a")
    with engine.begin() as connection:
        connection.execute(
            insert(chunk_search_index).values(
                id="search-doc-a",
                chunk_id="chunk-doc-a",
                document_id="doc-a",
                chunk_type=ChunkType.TEXT.value,
                search_text="Hydraulic maintenance interval and safety notes.",
                embedding=[1.0],
            )
        )

    response = client.delete("/documents/doc-a", headers=auth_header(token))

    assert response.status_code == 200
    assert response.json() == {"document_id": "doc-a", "status": "deleting"}
    assert len(publisher.messages) == 1
    assert publisher.messages[0].document_id == "doc-a"
    assert publisher.messages[0].task_type == "delete"

    with engine.connect() as connection:
        document = connection.execute(select(documents).where(documents.c.id == "doc-a")).mappings().one()
        tasks = connection.execute(select(ingest_tasks).where(ingest_tasks.c.document_id == "doc-a")).mappings().all()
        index_count = len(connection.execute(select(chunk_search_index).where(chunk_search_index.c.document_id == "doc-a")).all())
    assert document["status"] == "deleting"
    assert index_count == 1
    assert len(tasks) == 2
    assert any(task["task_type"] == "delete" and task["status"] == "queued" for task in tasks)


def test_delete_rejects_deleted_or_already_deleting_documents(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path)
    seed_document(engine, document_id="doc-deleted", task_id="task-deleted", status=DocumentStatus.DELETED.value)
    seed_document(engine, document_id="doc-deleting", task_id="task-deleting", status=DocumentStatus.DELETING.value)

    deleted_response = client.delete("/documents/doc-deleted", headers=auth_header(token))
    deleting_response = client.delete("/documents/doc-deleting", headers=auth_header(token))

    assert deleted_response.status_code == 409
    assert deleted_response.json()["error"]["code"] == "validation_error"
    assert deleting_response.status_code == 409
    assert deleting_response.json()["error"]["code"] == "validation_error"


def test_non_maintainer_write_returns_forbidden(tmp_path: Path) -> None:
    client, engine, token = library_client(tmp_path, role=UserRole.USER)
    seed_document(engine, document_id="doc-a", task_id="task-a")

    response = client.post("/documents/doc-a/reprocess", headers=auth_header(token))

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
