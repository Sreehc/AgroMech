from __future__ import annotations

from fastapi import Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from agromech_api.security.auth import (
    UserContext,
    authenticate_database_user,
    create_access_token,
    create_database_user,
    current_user_dependency,
)
from agromech_api.core.config import Settings
from agromech_api.core.errors import AppError, ErrorCode
from agromech_api.db.enums import UserRole


# 开放注册的输入约束：用户名 3-120 字符，密码至少 8 位。约束在此集中声明，
# 前后端提示保持一致。
USERNAME_MIN_LENGTH = 3
USERNAME_MAX_LENGTH = 120
PASSWORD_MIN_LENGTH = 8


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=USERNAME_MIN_LENGTH, max_length=USERNAME_MAX_LENGTH)
    password: str = Field(..., min_length=PASSWORD_MIN_LENGTH)
    display_name: str | None = Field(default=None, max_length=255)


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

    @app.post(
        "/auth/register",
        response_model=LoginResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["auth"],
    )
    def register(payload: RegisterRequest) -> LoginResponse:
        # 开放注册：新用户固定为 user 角色（不可自助提权）。用户名规范化后建号，
        # 冲突返回 409。建号成功即签发令牌，前端自动登录。
        username = payload.username.strip()
        if len(username) < USERNAME_MIN_LENGTH:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "Username too short",
                status_code=status.HTTP_400_BAD_REQUEST,
                details={"min_length": USERNAME_MIN_LENGTH},
            )
        user = create_database_user(
            engine,
            username=username,
            password=payload.password,
            role=UserRole.USER,
            display_name=(payload.display_name or None),
        )
        if user is None:
            raise AppError(
                ErrorCode.VALIDATION_ERROR,
                "Username already taken",
                status_code=status.HTTP_409_CONFLICT,
                details={"username": username},
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
