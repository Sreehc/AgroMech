from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine

from agromech_api.auth import create_access_token
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import metadata
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
    assert payload["answer"]
    assert payload["citations"][0]["document_id"] == "doc-m7040"


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
