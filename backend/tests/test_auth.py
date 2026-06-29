from pathlib import Path

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, update

from agromech_api.auth import create_access_token, create_database_user, hash_password, require_roles, verify_password
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import auth_audit_logs, metadata, users
from agromech_api.main import create_app


def auth_settings() -> Settings:
    return Settings(
        auth_token_secret="test-secret",
        session_ttl_minutes=30,
    )


def auth_client(tmp_path: Path):
    settings = auth_settings()
    engine = create_engine(f"sqlite:///{tmp_path / 'auth.db'}")
    metadata.create_all(engine)
    return TestClient(create_app(settings=settings, database_engine=engine)), engine, settings


def seed_user(engine, *, username="admin", password="secret", role=UserRole.ADMIN, status="active", token_version=1):
    with engine.begin() as connection:
        connection.execute(
            users.insert().values(
                id=f"user-{username}",
                username=username,
                password_hash=hash_password(password),
                role=role.value,
                status=status,
                display_name=username.title(),
                token_version=token_version,
            )
        )


def test_create_database_user_hashes_password_and_rejects_duplicates(tmp_path: Path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'create-user.db'}")
    metadata.create_all(engine)

    user = create_database_user(
        engine,
        username="admin",
        password="secret",
        role=UserRole.ADMIN,
        display_name="Administrator",
    )

    assert user.username == "admin"
    assert user.role == UserRole.ADMIN
    with engine.connect() as connection:
        row = connection.execute(select(users).where(users.c.username == "admin")).mappings().one()
    assert row["password_hash"] != "secret"
    assert verify_password("secret", row["password_hash"])
    assert row["display_name"] == "Administrator"
    assert row["status"] == "active"
    assert row["token_version"] == 1

    duplicate = create_database_user(
        engine,
        username="admin",
        password="new-secret",
        role=UserRole.ADMIN,
    )

    assert duplicate is None


def test_login_returns_bearer_token_and_current_user(tmp_path: Path) -> None:
    client, engine, _settings = auth_client(tmp_path)
    seed_user(engine)

    login = client.post("/auth/login", json={"username": "admin", "password": "secret"})

    assert login.status_code == 200
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"
    assert login.json()["expires_in"] == 1800

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json() == {"username": "admin", "role": "admin"}
    with engine.connect() as connection:
        audit_log = connection.execute(select(auth_audit_logs)).mappings().one()
    assert audit_log["event_type"] == "login"
    assert audit_log["success"] is True
    assert audit_log["username"] == "admin"


def test_missing_token_returns_unauthorized_for_protected_endpoint(tmp_path: Path) -> None:
    client, _engine, _settings = auth_client(tmp_path)

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_invalid_login_returns_unauthorized(tmp_path: Path) -> None:
    client, engine, _settings = auth_client(tmp_path)
    seed_user(engine)

    response = client.post("/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    with engine.connect() as connection:
        audit_log = connection.execute(select(auth_audit_logs)).mappings().one()
    assert audit_log["event_type"] == "login"
    assert audit_log["success"] is False


def test_database_role_logins_return_assigned_roles(tmp_path: Path) -> None:
    client, engine, _settings = auth_client(tmp_path)
    seed_user(engine, username="maintainer", password="maint-secret", role=UserRole.MAINTAINER)
    seed_user(engine, username="operator", password="user-secret", role=UserRole.USER)
    seed_user(engine, username="evaluator", password="eval-secret", role=UserRole.EVALUATOR)

    expected_roles = {
        "maintainer": ("maint-secret", "maintainer"),
        "operator": ("user-secret", "user"),
        "evaluator": ("eval-secret", "evaluator"),
    }

    for username, (password, role) in expected_roles.items():
        login = client.post("/auth/login", json={"username": username, "password": password})

        assert login.status_code == 200
        token = login.json()["access_token"]

        me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

        assert me.status_code == 200
        assert me.json() == {"username": username, "role": role}


def test_disabled_database_user_cannot_login(tmp_path: Path) -> None:
    client, engine, _settings = auth_client(tmp_path)
    seed_user(engine, status="disabled")

    response = client.post("/auth/login", json={"username": "admin", "password": "secret"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_token_version_mismatch_rejects_existing_token(tmp_path: Path) -> None:
    client, engine, settings = auth_client(tmp_path)
    seed_user(engine, username="operator", role=UserRole.USER, token_version=1)
    token = create_access_token(
        username="operator",
        role=UserRole.USER,
        settings=settings,
        user_id="user-operator",
        token_version=1,
    )
    with engine.begin() as connection:
        connection.execute(update(users).where(users.c.username == "operator").values(token_version=2))

    response = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_role_dependency_returns_forbidden_for_disallowed_role(tmp_path: Path) -> None:
    settings = auth_settings()
    engine = create_engine(f"sqlite:///{tmp_path / 'auth-role.db'}")
    metadata.create_all(engine)
    seed_user(engine, username="readonly", role=UserRole.USER)
    app = create_app(settings=settings, database_engine=engine)

    @app.get("/admin-only")
    def admin_only(_user=Depends(require_roles(UserRole.ADMIN))):
        return {"ok": True}

    user_token = create_access_token(
        username="readonly",
        role=UserRole.USER,
        settings=settings,
        user_id="user-readonly",
        token_version=1,
    )

    response = TestClient(app).get(
        "/admin-only",
        headers={"Authorization": f"Bearer {user_token}"},
    )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_write_requests_are_blocked_without_login() -> None:
    app = create_app(settings=auth_settings())

    @app.post("/write-probe")
    def write_probe():
        return {"ok": True}

    response = TestClient(app).post("/write-probe")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
