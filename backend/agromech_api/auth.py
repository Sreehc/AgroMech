from __future__ import annotations

import base64
import hashlib
import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Annotated, Callable

from fastapi import Depends, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from agromech_api.config import Settings, get_settings
from agromech_api.db.enums import UserRole
from agromech_api.errors import AppError, ErrorCode, error_payload


bearer_scheme = HTTPBearer(auto_error=False)


@dataclass(frozen=True)
class UserContext:
    username: str
    role: UserRole


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
    now: datetime | None = None,
) -> str:
    issued_at = now or datetime.now(UTC)
    expires_at = issued_at + timedelta(minutes=settings.session_ttl_minutes)
    payload = _encode(
        json.dumps(
            {
                "sub": username,
                "role": role.value,
                "exp": int(expires_at.timestamp()),
            },
            separators=(",", ":"),
        ).encode("utf-8")
    )
    return f"{payload}.{_signature(payload, settings.auth_token_secret)}"


def verify_access_token(token: str, settings: Settings) -> UserContext:
    try:
        payload, signature = token.split(".", 1)
        expected_signature = _signature(payload, settings.auth_token_secret)
        if not hmac.compare_digest(signature, expected_signature):
            raise ValueError("invalid signature")
        claims = json.loads(_decode(payload))
        if int(claims["exp"]) < int(datetime.now(UTC).timestamp()):
            raise ValueError("token expired")
        return UserContext(username=str(claims["sub"]), role=UserRole(str(claims["role"])))
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or expired access token", status_code=401) from exc


def authenticate_single_admin(username: str, password: str, settings: Settings) -> UserContext:
    if settings.auth_mode != "single_admin":
        raise AppError(ErrorCode.INTERNAL_ERROR, "Unsupported auth mode", status_code=500)
    if not hmac.compare_digest(username, settings.admin_username):
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid username or password", status_code=401)
    if not hmac.compare_digest(password, settings.admin_password):
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid username or password", status_code=401)
    return UserContext(username=username, role=UserRole.ADMIN)


def current_user_dependency() -> Callable:
    def dependency(
        request: Request,
        credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer_scheme)],
    ) -> UserContext:
        if credentials is None or credentials.scheme.lower() != "bearer":
            raise AppError(ErrorCode.UNAUTHORIZED, "Authentication required", status_code=401)
        settings = getattr(request.app.state, "settings", get_settings())
        return verify_access_token(credentials.credentials, settings)

    return dependency


def require_roles(*roles: UserRole) -> Callable:
    def dependency(
        user: Annotated[UserContext, Depends(current_user_dependency())],
    ) -> UserContext:
        if user.role not in roles:
            raise AppError(ErrorCode.FORBIDDEN, "Permission denied", status_code=403)
        return user

    return dependency


async def require_authenticated_write(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and request.url.path != "/auth/login":
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
        try:
            verify_access_token(credentials.credentials, settings)
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
