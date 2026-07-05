from __future__ import annotations

from fastapi import Depends, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from agromech_api.security.auth import UserContext, optional_user_dependency
from agromech_api.security.rate_limit import SlidingWindowRateLimiter
from agromech_api.core.config import Settings
from agromech_api.core.errors import AppError, ErrorCode
from agromech_api.qa.text import answer_text_question


class TextQaRequest(BaseModel):
    question: str = Field(...)
    filters: dict[str, str | None] = Field(default_factory=dict)
    session_id: str | None = None
    mode: str = "standard"


def client_ip(request: Request) -> str:
    # 优先取反代注入的 X-Forwarded-For 首段，回退到直连客户端地址。
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        first = forwarded.split(",")[0].strip()
        if first:
            return first
    return request.client.host if request.client else "unknown"


def register_text_qa_routes(app, *, settings: Settings, engine: Engine) -> None:
    # 匿名问答按 IP 限流（进程内滑动窗口）。登录用户不受此限。
    anonymous_limiter = SlidingWindowRateLimiter(
        max_requests=settings.anonymous_qa_rate_limit,
        window_seconds=settings.anonymous_qa_rate_window_seconds,
    )

    @app.post("/qa/text", tags=["qa"])
    def text_qa(
        payload: TextQaRequest,
        request: Request,
        user: UserContext | None = Depends(optional_user_dependency()),
    ) -> dict[str, object]:
        # 匿名访客：只能检索公用知识库，且不能绑定会话（会话需登录）；按 IP 限流。
        if user is None:
            if payload.session_id:
                raise AppError(
                    ErrorCode.UNAUTHORIZED,
                    "Sign in to use chat sessions",
                    status_code=status.HTTP_401_UNAUTHORIZED,
                )
            if not anonymous_limiter.allow(client_ip(request)):
                raise AppError(
                    ErrorCode.RATE_LIMITED,
                    "Too many anonymous requests, please slow down or sign in",
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                )
            return answer_text_question(
                engine,
                question=payload.question,
                filters=payload.filters,
                trace_id=request.state.trace_id,
                settings=settings,
                username=None,
                viewer_user_id=None,
                session_id=None,
            )
        return answer_text_question(
            engine,
            question=payload.question,
            filters=payload.filters,
            trace_id=request.state.trace_id,
            settings=settings,
            username=user.username,
            viewer_user_id=user.user_id,
            session_id=payload.session_id,
        )
