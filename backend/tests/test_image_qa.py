from pathlib import Path

from fastapi.testclient import TestClient
import pytest
from sqlalchemy import create_engine, insert, select

from auth_helpers import auth_token_for_user
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import chat_sessions, metadata, qa_messages, retrieval_logs
from agromech_api.image_qa import visual_annotation_status, visual_search_query
from agromech_api.main import create_app
from test_hybrid_retrieval import seed_retrieval_corpus


def image_qa_settings(tmp_path: Path) -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
        graph_backend="local",
        vector_backend="local",
        model_provider="local",
        embedding_provider="local",
        embedding_dimension=256,
    )


def image_qa_client(tmp_path: Path, role: UserRole = UserRole.USER, username: str | None = None) -> tuple[TestClient, object, str]:
    settings = image_qa_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = auth_token_for_user(engine, settings, username=username or role.value, role=role)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, token


def auth_header(token: str, trace_id: str = "trace-image") -> dict[str, str]:
    return {"Authorization": f"Bearer {token}", "X-Trace-Id": trace_id}


def seed_chat_session(engine, *, session_id: str, username: str) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(chat_sessions).values(
                id=session_id,
                username=username,
                title="图片排查",
                messages=[],
                filters={},
                has_image=False,
            )
        )


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
    assert payload["agent_trace"][0]["step"] == "route"
    assert payload["agent_trace"][0]["decision"] == "text_visual"


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

    assert "E01 液压告警怎么排查？" in retrieval_log["query"]
    assert retrieval_log["filters"] == {
        "brand": "Kubota",
        "model": "M7040",
        "document_type": "manual",
        "language": "zh-CN",
    }


def test_image_qa_routes_text_visual_even_when_question_has_no_visual_words(tmp_path: Path) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-image-route"),
        data={"question": "E01 怎么排查？"},
        files={"image": ("m7040-hydraulic-e01.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["agent_trace"][0]["decision"] == "text_visual"
    assert payload["agent_trace"][0]["reason"] == "visual input is present"


def test_visual_search_query_includes_ocr_description_and_detected_entities() -> None:
    query = visual_search_query(
        "这个报警怎么处理？",
        {
            "ocr_text": "OCR: E01 HYD",
            "description": "dashboard shows hydraulic warning",
            "detected_entities": {
                "possible_models": ["M7040"],
                "visible_parts": ["hydraulic pump"],
                "warning_lights": ["E01"],
                "part_numbers": ["HH-123"],
            },
        },
    )

    assert "这个报警怎么处理？" in query
    assert "OCR: E01 HYD" in query
    assert "dashboard shows hydraulic warning" in query
    assert "M7040" in query
    assert "hydraulic pump" in query
    assert "E01" in query
    assert "HH-123" in query


def test_image_qa_low_confidence_with_question_still_uses_visual_query_clues(tmp_path: Path) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-low-with-question"),
        data={"question": "这个报警怎么处理？"},
        files={"image": ("unclear.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-low-with-question")
        ).mappings().one()

    assert "这个报警怎么处理？" in retrieval_log["query"]
    assert "No reliable visual clue detected" in retrieval_log["query"]


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


def test_image_qa_uses_ocr_and_vision_results_in_payload_and_retrieval_query(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    monkeypatch.setattr(
        "agromech_api.image_qa.default_ocr_reader",
        lambda _path: "OCR plate: Kubota M7040 E01",
    )

    def fake_visual_reader(_path: Path, ocr_text: str | None) -> dict[str, object]:
        assert ocr_text == "OCR plate: Kubota M7040 E01"
        return {
            "description": "dashboard shows hydraulic warning and pump housing",
            "possible_models": ["M7040"],
            "visible_parts": ["hydraulic pump"],
            "warning_lights": ["E01"],
            "part_numbers": ["HH-123"],
            "confidence": 0.91,
        }

    monkeypatch.setattr(
        "agromech_api.image_qa.build_visual_reader",
        lambda _settings: fake_visual_reader,
    )

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-image-real-vision"),
        data={"question": "这个报警怎么排查？"},
        files={"image": ("camera-upload.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ocr_text"] == "OCR plate: Kubota M7040 E01"
    assert payload["visual_observation"] == "dashboard shows hydraulic warning and pump housing"
    assert payload["detected_entities"] == {
        "possible_models": ["M7040"],
        "visible_parts": ["hydraulic pump"],
        "warning_lights": ["E01"],
        "part_numbers": ["HH-123"],
    }
    assert payload["visual_confidence"]["confidence"] == 0.91
    assert payload["visual_confidence"]["low_confidence"] is False

    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-image-real-vision")
        ).mappings().one()

    assert "OCR plate: Kubota M7040 E01" in retrieval_log["query"]
    assert "dashboard shows hydraulic warning and pump housing" in retrieval_log["query"]
    assert "hydraulic pump" in retrieval_log["query"]
    assert "HH-123" in retrieval_log["query"]


def test_image_qa_degrades_to_ocr_when_vision_reader_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, engine, token = image_qa_client(tmp_path)
    seed_retrieval_corpus(engine)

    monkeypatch.setattr(
        "agromech_api.image_qa.default_ocr_reader",
        lambda _path: "OCR warning: Kubota M7040 E01 hydraulic",
    )

    def unavailable_visual_reader(_path: Path, _ocr_text: str | None) -> dict[str, object]:
        raise RuntimeError("Vision model unavailable")

    monkeypatch.setattr(
        "agromech_api.image_qa.build_visual_reader",
        lambda _settings: unavailable_visual_reader,
    )

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-image-ocr-degraded"),
        data={"question": "这个报警怎么排查？"},
        files={"image": ("camera-upload.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["ocr_text"] == "OCR warning: Kubota M7040 E01 hydraulic"
    assert payload["visual_confidence"]["degraded_reason"] == "vision_unavailable"
    assert payload["answer"]
    assert payload["citations"]

    with engine.connect() as connection:
        retrieval_log = connection.execute(
            select(retrieval_logs).where(retrieval_logs.c.trace_id == "trace-image-ocr-degraded")
        ).mappings().one()

    assert "OCR warning: Kubota M7040 E01 hydraulic" in retrieval_log["query"]


def test_image_qa_persists_user_and_assistant_messages_for_session_id(tmp_path: Path) -> None:
    client, engine, token = image_qa_client(tmp_path, username="tech")
    seed_retrieval_corpus(engine)
    seed_chat_session(engine, session_id="session-image-history", username="tech")

    response = client.post(
        "/qa/image",
        headers=auth_header(token, "trace-image-session"),
        data={"question": "这张图的 E01 液压告警怎么排查？", "session_id": "session-image-history"},
        files={"image": ("m7040-hydraulic-e01.png", b"fake-image", "image/png")},
    )

    assert response.status_code == 200
    with engine.connect() as connection:
        session = connection.execute(
            select(chat_sessions).where(chat_sessions.c.id == "session-image-history")
        ).mappings().one()
        messages = connection.execute(
            select(qa_messages)
            .where(qa_messages.c.session_id == "session-image-history")
            .order_by(qa_messages.c.created_at, qa_messages.c.id)
        ).mappings().all()

    assert len(messages) == 2
    assert messages[0]["role"] == "user"
    assert messages[0]["metadata"]["trace_id"] == "trace-image-session"
    assert messages[0]["metadata"]["has_image"] is True
    assert messages[1]["role"] == "assistant"
    assert messages[1]["metadata"]["trace_id"] == "trace-image-session"
    assert messages[1]["metadata"]["citations"]
    assert session["has_image"] is True
    assert session["messages"][0]["role"] == "user"
    assert session["messages"][1]["role"] == "assistant"
