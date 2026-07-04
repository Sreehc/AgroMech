from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from fastapi import Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine, desc, func, insert, select, update

from agromech_api.security.auth import UserContext, require_roles
from agromech_api.db.enums import UserRole
from agromech_api.db.models import chat_sessions
from agromech_api.core.errors import AppError, ErrorCode


SESSION_LIST_LIMIT = 50


class ChatSessionCreateRequest(BaseModel):
    title: str = "未命名会话"
    messages: list[dict[str, Any]] = Field(default_factory=list)
    filters: dict[str, Any] = Field(default_factory=dict)
    has_image: bool = False


class ChatSessionUpdateRequest(BaseModel):
    title: str | None = None
    messages: list[dict[str, Any]] | None = None
    filters: dict[str, Any] | None = None
    has_image: bool | None = None


def session_payload(row) -> dict[str, object]:
    return {
        "id": row["id"],
        "title": row["title"],
        "messages": row["messages"],
        "filters": row["filters"],
        "has_image": row["has_image"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_user_session_or_404(engine: Engine, *, username: str, session_id: str):
    with engine.connect() as connection:
        row = connection.execute(
            select(chat_sessions).where(
                chat_sessions.c.id == session_id,
                chat_sessions.c.username == username,
            )
        ).mappings().one_or_none()
    if row is None:
        raise AppError(ErrorCode.NOT_FOUND, "Chat session not found", status_code=status.HTTP_404_NOT_FOUND)
    return row


def normalized_limit(limit: int) -> int:
    return max(1, min(limit, SESSION_LIST_LIMIT))


def register_chat_session_routes(app, *, engine: Engine) -> None:
    allowed_roles = (UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)

    @app.get("/chat-sessions", tags=["chat-sessions"])
    def list_chat_sessions(
        limit: int = Query(default=SESSION_LIST_LIMIT, ge=1),
        user: UserContext = Depends(require_roles(*allowed_roles)),
    ) -> dict[str, object]:
        capped_limit = normalized_limit(limit)
        with engine.connect() as connection:
            total = connection.execute(
                select(func.count()).select_from(chat_sessions).where(chat_sessions.c.username == user.username)
            ).scalar_one()
            rows = connection.execute(
                select(chat_sessions)
                .where(chat_sessions.c.username == user.username)
                .order_by(desc(chat_sessions.c.updated_at))
                .limit(capped_limit)
            ).mappings().all()

        return {"total": total, "items": [session_payload(row) for row in rows]}

    @app.post("/chat-sessions", status_code=status.HTTP_201_CREATED, tags=["chat-sessions"])
    def create_chat_session(
        payload: ChatSessionCreateRequest,
        user: UserContext = Depends(require_roles(*allowed_roles)),
    ) -> dict[str, object]:
        now = datetime.now(UTC)
        session_id = str(uuid4())
        values = {
            "id": session_id,
            "username": user.username,
            "title": payload.title.strip() or "未命名会话",
            "messages": payload.messages,
            "filters": payload.filters,
            "has_image": payload.has_image,
            "created_at": now,
            "updated_at": now,
        }
        with engine.begin() as connection:
            connection.execute(insert(chat_sessions).values(**values))

        return session_payload(values)

    @app.get("/chat-sessions/{session_id}", tags=["chat-sessions"])
    def get_chat_session(
        session_id: str,
        user: UserContext = Depends(require_roles(*allowed_roles)),
    ) -> dict[str, object]:
        return session_payload(get_user_session_or_404(engine, username=user.username, session_id=session_id))

    @app.patch("/chat-sessions/{session_id}", tags=["chat-sessions"])
    def update_chat_session(
        session_id: str,
        payload: ChatSessionUpdateRequest,
        user: UserContext = Depends(require_roles(*allowed_roles)),
    ) -> dict[str, object]:
        get_user_session_or_404(engine, username=user.username, session_id=session_id)
        values: dict[str, object] = {"updated_at": datetime.now(UTC)}
        if payload.title is not None:
            values["title"] = payload.title.strip() or "未命名会话"
        if payload.messages is not None:
            values["messages"] = payload.messages
        if payload.filters is not None:
            values["filters"] = payload.filters
        if payload.has_image is not None:
            values["has_image"] = payload.has_image

        with engine.begin() as connection:
            connection.execute(
                update(chat_sessions)
                .where(
                    chat_sessions.c.id == session_id,
                    chat_sessions.c.username == user.username,
                )
                .values(**values)
            )

        return session_payload(get_user_session_or_404(engine, username=user.username, session_id=session_id))

    @app.delete("/chat-sessions/{session_id}", tags=["chat-sessions"])
    def delete_chat_session(
        session_id: str,
        user: UserContext = Depends(require_roles(*allowed_roles)),
    ) -> dict[str, object]:
        get_user_session_or_404(engine, username=user.username, session_id=session_id)
        with engine.begin() as connection:
            connection.execute(
                chat_sessions.delete().where(
                    chat_sessions.c.id == session_id,
                    chat_sessions.c.username == user.username,
                )
            )

        return {"session_id": session_id, "deleted": True}
