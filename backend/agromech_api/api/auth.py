from __future__ import annotations

from fastapi import Depends, Request
from pydantic import BaseModel
from sqlalchemy import Engine

from agromech_api.security.auth import (
    UserContext,
    authenticate_database_user,
    create_access_token,
    current_user_dependency,
)
from agromech_api.core.config import Settings


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def register_auth_routes(app, *, settings: Settings, engine: Engine) -> None:
    @app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
    def login(payload: LoginRequest, request: Request) -> LoginResponse:
        user = authenticate_database_user(
            payload.username,
            payload.password,
            engine=engine,
            request=request,
        )
        token = create_access_token(
            username=user.username,
            role=user.role,
            settings=settings,
            user_id=user.user_id,
            token_version=user.token_version,
        )
        return LoginResponse(
            access_token=token,
            expires_in=settings.session_ttl_minutes * 60,
        )

    @app.get("/auth/me", tags=["auth"])
    def me(user: UserContext = Depends(current_user_dependency())) -> dict[str, str]:
        return {"username": user.username, "role": user.role.value}
