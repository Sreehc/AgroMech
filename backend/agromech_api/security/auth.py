from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Callable
from uuid import uuid4

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import Engine, select, update

from agromech_api.core.config import Settings, get_settings
from agromech_api.db.enums import UserRole
from agromech_api.db.models import auth_audit_logs, users
from agromech_api.core.errors import AppError, ErrorCode, error_payload


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class UserContext:
    username: str
    role: UserRole
    user_id: str | None = None
    token_version: int | None = None


PASSWORD_HASH_ITERATIONS = 210_000


def _encode(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def _signature(payload: str, secret: str) -> str:
    digest = hmac.new(secret.encode("utf-8"), payload.encode("ascii"), hashlib.sha256).digest()
    return _encode(digest)


def create_access_token(
    *,
    username: str,
    role: UserRole,
    settings: Settings,
    user_id: str | None = None,
    token_version: int = 1,
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.session_ttl_minutes)
    payload = _encode(
        json.dumps(
            {
                "sub": username,
                "username": username,
                "user_id": user_id,
                "role": role.value,
                "token_version": token_version,
                "exp": int(expires_at.timestamp()),
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{payload}.{_signature(payload, settings.auth_token_secret)}"


def hash_password(password: str, *, salt: str | None = None, iterations: int = PASSWORD_HASH_ITERATIONS) -> str:
    active_salt = salt or secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        active_salt.encode("ascii"),
        iterations,
    )
    return f"pbkdf2_sha256${iterations}${active_salt}${_encode(digest)}"


def verify_password(password: str, password_hash: str) -> bool:
    try:
        algorithm, iterations, salt, expected_digest = password_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        candidate = hash_password(password, salt=salt, iterations=int(iterations)).rsplit("$", 1)[1]
        return hmac.compare_digest(candidate, expected_digest)
    except (ValueError, TypeError):
        return False


def create_database_user(
    engine: Engine,
    *,
    username: str,
    password: str,
    role: UserRole,
    display_name: str | None = None,
    status: str = "active",
) -> UserContext | None:
    with engine.begin() as connection:
        existing_user = connection.execute(
            select(users.c.id).where(users.c.username == username)
        ).one_or_none()
        if existing_user is not None:
            return None
        user_id = str(uuid4())
        connection.execute(
            users.insert().values(
                id=user_id,
                username=username,
                password_hash=hash_password(password),
                role=role.value,
                status=status,
                display_name=display_name,
                token_version=1,
            )
        )
    return UserContext(username=username, role=role, user_id=user_id, token_version=1)


def verify_access_token(token: str, settings: Settings, engine: Engine | None = None) -> UserContext:
    try:
        payload, signature = token.split(".", 1)
        expected_signature = _signature(payload, settings.auth_token_secret)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("invalid signature")
        claims = json.loads(_decode(payload))
        if int(claims["exp"]) < int(datetime.now(UTC).timestamp()):
            raise ValueError("token expired")
        username = str(claims.get("username") or claims["sub"])
        role = UserRole(str(claims["role"]))
        user_id = str(claims.get("user_id") or "")
        token_version = claims.get("token_version")
        if engine is not None:
            if token_version is None:
                raise ValueError("token version missing")
            return database_user_context(
                engine,
                user_id=user_id,
                username=username,
                role=role,
                token_version=token_version,
            )
        return UserContext(username=username, role=role, user_id=user_id or None)
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or expired access token", status_code=401) from exc


def database_user_context(
    engine: Engine,
    *,
    user_id: str,
    username: str,
    role: UserRole,
    token_version: object,
) -> UserContext:
    with engine.connect() as connection:
        clauses = [users.c.username == username]
        if user_id:
            clauses.insert(0, users.c.id == user_id)
        user = connection.execute(select(users).where(*clauses)).mappings().one_or_none()
    if user is None or user["status"] != "active":
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or expired access token", status_code=401)
    if user["role"] != role.value:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or expired access token", status_code=401)
    if token_version is not None and int(token_version) != int(user["token_version"]):
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or expired access token", status_code=401)
    return UserContext(
        username=user["username"],
        role=UserRole(user["role"]),
        user_id=user["id"],
        token_version=int(user["token_version"]),
    )


def authenticate_database_user(
    username: str,
    password: str,
    *,
    engine: Engine,
    request: Request | None = None,
) -> UserContext:
    with engine.begin() as connection:
        user = connection.execute(select(users).where(users.c.username == username)).mappings().one_or_none()
        success = bool(
            user is not None
            and user["status"] == "active"
            and verify_password(password, user["password_hash"])
        )
        connection.execute(
            auth_audit_logs.insert().values(
                id=str(uuid4()),
                user_id=user["id"] if user is not None else None,
                username=username,
                event_type="login",
                success=success,
                ip_address=request.client.host if request and request.client else None,
                user_agent=request.headers.get("user-agent") if request else None,
                metadata={},
            )
        )
        if success:
            connection.execute(
                update(users)
                .where(users.c.id == user["id"])
                .values(last_login_at=datetime.now(UTC))
            )
    if not success or user is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid username or password", status_code=401)
    return UserContext(
        username=user["username"],
        role=UserRole(user["role"]),
        user_id=user["id"],
        token_version=int(user["token_version"]),
    )


def current_user_dependency() -> Callable:
    def dependency(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    ) -> UserContext:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AppError(ErrorCode.UNAUTHORIZED, "Authentication required", status_code=401)
        settings = getattr(request.app.state, "settings", get_settings())
        engine = getattr(request.app.state, "database_engine", None)
        return verify_access_token(credentials.credentials, settings, engine)

    return dependency


def require_roles(*roles: UserRole) -> Callable:
    def dependency(
        user: Annotated[UserContext, Depends(current_user_dependency())],
    ) -> UserContext:
        if user.role not in roles:
            raise AppError(ErrorCode.FORBIDDEN, "Permission denied", status_code=403)
        return user

    return dependency


def optional_user_dependency() -> Callable:
    """可选鉴权：带有效 Bearer 令牌时返回 UserContext，未携带令牌时返回 None。

    用于匿名可访问的端点（如匿名问答）。注意：令牌存在但无效/过期时仍会抛
    401，避免坏令牌被静默降级为匿名。
    """

    def dependency(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    ) -> UserContext | None:
        if credentials is None or credentials.scheme.lower() != "bearer":
            return None
        settings = getattr(request.app.state, "settings", get_settings())
        engine = getattr(request.app.state, "database_engine", None)
        return verify_access_token(credentials.credentials, settings, engine)

    return dependency


# 这些路径自行处理鉴权（匿名问答走可选鉴权 + 限流，开放注册无需令牌），
# 因此豁免全局写鉴权中间件；否则会在到达路由前被 401 拦下。
# 注意：/qa/image 不在此列——匿名对话仅限文本问答，图片问答（走视觉模型，
# 成本高、易被滥用）仍要求登录。
WRITE_AUTH_EXEMPT_PATHS = frozenset({"/auth/login", "/auth/register", "/qa/text"})


async def require_authenticated_write(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path not in WRITE_AUTH_EXEMPT_PATHS:
        credentials = await bearer_scheme(request)
        if credentials is None:
            return JSONResponse(
                status_code=401,
                content=error_payload(
                    ErrorCode.UNAUTHORIZED,
                    "Authentication required",
                    trace_id=getattr(request.state, "trace_id", None),
                ),
            )
        settings = getattr(request.app.state, "settings", get_settings())
        engine = getattr(request.app.state, "database_engine", None)
        try:
            verify_access_token(credentials.credentials, settings, engine)
        except AppError as exc:
            return JSONResponse(
                status_code=exc.status_code,
                content=error_payload(
                    exc.code,
                    exc.message,
                    details=exc.details,
                    trace_id=getattr(request.state, "trace_id", None),
                ),
            )
    return await call_next(request)
