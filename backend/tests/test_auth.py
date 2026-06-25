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


def static_role_settings() -> Settings:
    return Settings(
        auth_mode="static_roles",
        admin_username="admin",
        admin_password="secret",
        maintainer_username="maintainer",
        maintainer_password="maint-secret",
        user_username="operator",
        user_password="user-secret",
        evaluator_username="evaluator",
        evaluator_password="eval-secret",
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


def test_static_role_logins_return_assigned_roles() -> None:
    client = TestClient(create_app(settings=static_role_settings()))

    expected_roles = {
        "admin": ("secret", "admin"),
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
