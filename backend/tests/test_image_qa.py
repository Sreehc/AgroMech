from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select

from agromech_api.auth import create_access_token
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import metadata, retrieval_logs
from agromech_api.image_qa import visual_annotation_status
from agromech_api.main import create_app
from test_hybrid_retrieval import seed_retrieval_corpus


def image_qa_settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
    )


def image_qa_client(tmp_path: Path, role: UserRole = UserRole.USER) -> tuple[TestClient, object, str]:
    settings = image_qa_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = create_access_token(username=role.value, role=role, settings=settings)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, token


def auth_header(token: str, trace_id: str = "trace-image") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Trace-Id": trace_id}


def test_image_qa_returns_visual_observation_detected_entities_answer_and_citations(tmp_path: Path) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    response = client.post(
        "/qa/image",
        headers=auth_header(token),
        data={"question": "这张图的 E01 液压告警怎么排查？"},
        files={"image": ("m7040-hydraulic-e01.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "trace-image"
    assert payload["visual_observation"]
    assert payload["detected_entities"]["possible_models"] == ["M7040"]
    assert "hydraulic" in payload["detected_entities"]["visible_parts"]
    assert payload["visual_confidence"]["low_confidence"] is False
    assert payload["visual_annotation_status"] == {
        "status": "available",
        "coordinate_format": "normalized_xywh",
        "missing_reason": None,
    }
    model_annotation = next(
        annotation for annotation in payload["visual_annotations"] if annotation["type"] == "possible_model"
    )
    assert model_annotation["label"] == "M7040"
    assert model_annotation["confidence"] == 0.8
    assert model_annotation["bbox"]["format"] == "normalized_xywh"
    assert 0 <= model_annotation["bbox"]["x"] <= 1
    assert 0 <= model_annotation["bbox"]["y"] <= 1
    assert 0 < model_annotation["bbox"]["width"] <= 1
    assert 0 < model_annotation["bbox"]["height"] <= 1
    assert model_annotation["bbox"]["x"] + model_annotation["bbox"]["width"] <= 1
    assert model_annotation["bbox"]["y"] + model_annotation["bbox"]["height"] <= 1
    assert payload["answer"]
    assert payload["citations"][0]["document_id"] == "doc-m7040"


def test_image_qa_forwards_context_filters_to_retrieval_query(tmp_path: Path) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-image-filters"),
        data={
            "question": "E01 液压告警怎么排查？",
            "brand": "Kubota",
            "model": "M7040",
            "document_type": "manual",
            "language": "zh-CN",
        },
        files={"image": ("dashboard-e01.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-image-filters")
        ).mappings().one()

    assert "Kubota" in retrieval_log["query"]
    assert "M7040" in retrieval_log["query"]
    assert "manual" in retrieval_log["query"]
    assert "zh-CN" in retrieval_log["query"]


def test_image_qa_rejects_multiple_images(tmp_path: Path) -> None:
    client, _engine, token = image_qa_client(tmp_path)

    response = client.post(
        "/qa/image",
        headers=auth_header(token),
        files=[
            ("image", ("first.png", b"first", "image/png")),
            ("image", ("second.png", b"second", "image/png")),
        ],
    )

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "too_many_images"


def test_image_qa_low_confidence_without_question_asks_for_more_information(tmp_path: Path) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-low"),
        files={"image": ("unclear.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["trace_id"] == "trace-low"
    assert payload["visual_confidence"]["low_confidence"] is True
    assert "补充" in payload["answer"]
    assert payload["citations"] == []
    assert payload["detected_entities"] == {
        "possible_models": [],
        "visible_parts": [],
        "warning_lights": [],
        "part_numbers": [],
    }
    assert payload["visual_annotations"] == []
    assert payload["visual_annotation_status"] == {
        "status": "missing",
        "coordinate_format": "normalized_xywh",
        "missing_reason": "no_detected_entities",
    }


def test_visual_annotation_status_reports_missing_when_annotations_have_no_bbox() -> None:
    status = visual_annotation_status([{"id": "warning-1", "type": "warning_light", "label": "E01"}])

    assert status == {
        "status": "missing",
        "coordinate_format": "normalized_xywh",
        "missing_reason": "no_bbox",
    }
