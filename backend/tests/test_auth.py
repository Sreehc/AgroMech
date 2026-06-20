from fastapi import Depends
from fastapi.testclient import TestClient

from agromech_api.auth import create_access_token, require_roles
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.main import create_app


def auth_settings() -> Settings:
    return Settings(
        admin_username="admin",
        admin_password="secret",
        auth_token_secret="test-secret",
        session_ttl_minutes=30,
    )


def test_login_returns_bearer_token_and_current_user() -> None:
    client = TestClient(create_app(settings=auth_settings()))

    login = client.post("/auth/login", json={"username": "admin", "password": "secret"})

    assert login.status_code == 200
    token = login.json()["access_token"]
    assert login.json()["token_type"] == "bearer"
    assert login.json()["expires_in"] == 1800

    me = client.get("/auth/me", headers={"Authorization": f"Bearer {token}"})

    assert me.status_code == 200
    assert me.json() == {"username": "admin", "role": "admin"}


def test_missing_token_returns_unauthorized_for_protected_endpoint() -> None:
    client = TestClient(create_app(settings=auth_settings()))

    response = client.get("/auth/me")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_invalid_login_returns_unauthorized() -> None:
    client = TestClient(create_app(settings=auth_settings()))

    response = client.post("/auth/login", json={"username": "admin", "password": "wrong"})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_role_dependency_returns_forbidden_for_disallowed_role() -> None:
    settings = auth_settings()
    app = create_app(settings=settings)

    @app.get("/admin-only")
    def admin_only(_user=Depends(require_roles(UserRole.ADMIN))):
        return {"ok": True}

    user_token = create_access_token(
        username="readonly",
        role=UserRole.USER,
        settings=settings,
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
