from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, insert, select

from agromech_api.auth import create_access_token
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import chat_sessions, metadata
from agromech_api.main import create_app


def session_settings(tmp_path: Path) -> Settings:
    return Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        local_file_storage_path=str(tmp_path / "files"),
    )


def session_client(tmp_path: Path, *, username: str = "admin", role: UserRole = UserRole.USER) -> tuple[TestClient, object, str]:
    settings = session_settings(tmp_path)
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    token = create_access_token(username=username, role=role, settings=settings)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, token


def auth_header(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def seed_session(
    engine,
    *,
    session_id: str,
    username: str,
    title: str = "液压提升无力",
    updated_at: datetime | None = None,
    has_image: bool = False,
) -> None:
    timestamp = updated_at or datetime.now(UTC)
    with engine.begin() as connection:
        connection.execute(
            insert(chat_sessions).values(
                id=session_id,
                username=username,
                title=title,
                messages=[{"role": "user", "parts": [{"type": "text", "text": title}]}],
                filters={"brand": "Kubota"},
                has_image=has_image,
                created_at=timestamp,
                updated_at=timestamp,
            )
        )


def test_create_read_update_delete_session_for_current_user(tmp_path: Path) -> None:
    client, engine, token = session_client(tmp_path, username="tech")

    create = client.post(
        "/chat-sessions",
        headers=auth_header(token),
        json={
            "title": "液压提升无力",
            "messages": [{"role": "user", "parts": [{"type": "text", "text": "如何排查？"}]}],
            "filters": {"brand": "Kubota", "model": "M7040"},
            "has_image": False,
        },
    )

    assert create.status_code == 201
    created = create.json()
    assert created["id"]
    assert created["title"] == "液压提升无力"
    assert created["messages"][0]["role"] == "user"
    assert created["filters"] == {"brand": "Kubota", "model": "M7040"}
    assert created["has_image"] is False
    assert created["created_at"]
    assert created["updated_at"]

    detail = client.get(f"/chat-sessions/{created['id']}", headers=auth_header(token))
    assert detail.status_code == 200
    assert detail.json()["id"] == created["id"]

    update = client.patch(
        f"/chat-sessions/{created['id']}",
        headers=auth_header(token),
        json={
            "title": "更新后的会话",
            "messages": [
                {"role": "user", "parts": [{"type": "text", "text": "如何排查？"}]},
                {"role": "assistant", "parts": [{"type": "text", "text": "先检查液压油。"}]},
            ],
            "filters": {"brand": "Kubota", "model": "M7040", "document_type": "manual"},
            "has_image": True,
        },
    )

    assert update.status_code == 200
    assert update.json()["title"] == "更新后的会话"
    assert len(update.json()["messages"]) == 2
    assert update.json()["filters"]["document_type"] == "manual"
    assert update.json()["has_image"] is True

    delete = client.delete(f"/chat-sessions/{created['id']}", headers=auth_header(token))
    assert delete.status_code == 200
    assert delete.json() == {"session_id": created["id"], "deleted": True}

    with engine.connect() as connection:
        remaining = connection.execute(select(chat_sessions).where(chat_sessions.c.id == created["id"])).all()
    assert remaining == []


def test_list_sessions_is_user_isolated_ordered_and_limited_to_50(tmp_path: Path) -> None:
    client, engine, token = session_client(tmp_path, username="tech")
    base = datetime(2026, 1, 1, tzinfo=UTC)
    for index in range(55):
        seed_session(
            engine,
            session_id=f"session-{index:02d}",
            username="tech",
            title=f"session {index:02d}",
            updated_at=base + timedelta(minutes=index),
            has_image=index % 2 == 0,
        )
    seed_session(engine, session_id="other-user-session", username="other", title="should not leak")

    response = client.get("/chat-sessions", headers=auth_header(token))

    assert response.status_code == 200
    payload = response.json()
    assert payload["total"] == 55
    assert len(payload["items"]) == 50
    assert payload["items"][0]["id"] == "session-54"
    assert payload["items"][-1]["id"] == "session-05"
    assert "other-user-session" not in {item["id"] for item in payload["items"]}


def test_sessions_cannot_be_read_or_mutated_across_users(tmp_path: Path) -> None:
    client, engine, token = session_client(tmp_path, username="tech")
    seed_session(engine, session_id="other-session", username="other")

    read = client.get("/chat-sessions/other-session", headers=auth_header(token))
    update = client.patch("/chat-sessions/other-session", headers=auth_header(token), json={"title": "stolen"})
    delete = client.delete("/chat-sessions/other-session", headers=auth_header(token))

    assert read.status_code == 404
    assert update.status_code == 404
    assert delete.status_code == 404


def test_missing_token_returns_unauthorized_for_session_list(tmp_path: Path) -> None:
    client, _engine, _token = session_client(tmp_path)

    response = client.get("/chat-sessions")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
