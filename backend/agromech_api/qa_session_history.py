from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import Engine, insert, select, update

from agromech_api.chat_sessions import get_user_session_or_404
from agromech_api.db.models import chat_sessions, qa_messages


def ensure_session_belongs_to_user(engine: Engine, *, username: str, session_id: str) -> None:
    get_user_session_or_404(engine, username=username, session_id=session_id)


def append_text_session_exchange(
    engine: Engine,
    *,
    username: str,
    session_id: str,
    question: str,
    filters: dict[str, object],
    payload: dict[str, object],
) -> None:
    user_message = {
        "role": "user",
        "parts": [{"type": "text", "text": question}],
    }
    assistant_message = {
        "role": "assistant",
        "parts": [{"type": "text", "text": str(payload["answer"])}],
        "metadata": assistant_message_metadata(payload),
    }
    append_session_exchange(
        engine,
        username=username,
        session_id=session_id,
        filters=filters,
        has_image=False,
        user_message=user_message,
        user_metadata={
            "trace_id": payload["trace_id"],
            "filters": filters,
            "has_image": False,
        },
        assistant_message=assistant_message,
        assistant_metadata=assistant_message_metadata(payload),
    )


def append_image_session_exchange(
    engine: Engine,
    *,
    username: str,
    session_id: str,
    question: str | None,
    filename: str,
    filters: dict[str, object],
    payload: dict[str, object],
) -> None:
    user_parts: list[dict[str, object]] = []
    if question and question.strip():
        user_parts.append({"type": "text", "text": question.strip()})
    user_parts.append(
        {
            "type": "file",
            "mediaType": "image/*",
            "filename": filename,
        }
    )
    user_message = {
        "role": "user",
        "parts": user_parts,
    }
    assistant_message = {
        "role": "assistant",
        "parts": [{"type": "text", "text": str(payload["answer"])}],
        "metadata": assistant_message_metadata(payload),
    }
    append_session_exchange(
        engine,
        username=username,
        session_id=session_id,
        filters=filters,
        has_image=True,
        user_message=user_message,
        user_metadata={
            "trace_id": payload["trace_id"],
            "filters": filters,
            "has_image": True,
        },
        assistant_message=assistant_message,
        assistant_metadata=assistant_message_metadata(payload),
    )


def append_session_exchange(
    engine: Engine,
    *,
    username: str,
    session_id: str,
    filters: dict[str, object],
    has_image: bool,
    user_message: dict[str, object],
    user_metadata: dict[str, object],
    assistant_message: dict[str, object],
    assistant_metadata: dict[str, object],
) -> None:
    with engine.begin() as connection:
        session = connection.execute(
            select(chat_sessions).where(
                chat_sessions.c.id == session_id,
                chat_sessions.c.username == username,
            )
        ).mappings().one()
        existing_messages = list(session["messages"] or [])
        updated_messages = [*existing_messages, user_message, assistant_message]
        timestamp = datetime.now(UTC)
        connection.execute(
            insert(qa_messages).values(
                id=str(uuid4()),
                session_id=session_id,
                role="user",
                content=user_message,
                metadata=user_metadata,
                created_at=timestamp,
            )
        )
        connection.execute(
            insert(qa_messages).values(
                id=str(uuid4()),
                session_id=session_id,
                role="assistant",
                content=assistant_message,
                metadata=assistant_metadata,
                created_at=datetime.now(UTC),
            )
        )
        connection.execute(
            update(chat_sessions)
            .where(
                chat_sessions.c.id == session_id,
                chat_sessions.c.username == username,
            )
            .values(
                messages=updated_messages,
                filters=filters,
                has_image=has_image or bool(session["has_image"]),
                updated_at=timestamp,
            )
        )


def assistant_message_metadata(payload: dict[str, object]) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "trace_id": payload["trace_id"],
        "citations": payload.get("citations") or [],
        "uncertainty": payload.get("uncertainty"),
        "safety_warnings": payload.get("safety_warnings") or [],
    }
    if "visual_observation" in payload:
        metadata["visual_observation"] = payload.get("visual_observation")
    if "ocr_text" in payload:
        metadata["ocr_text"] = payload.get("ocr_text")
    if "detected_entities" in payload:
        metadata["detected_entities"] = payload.get("detected_entities")
    return metadata
