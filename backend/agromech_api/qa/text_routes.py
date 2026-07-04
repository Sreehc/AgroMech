from __future__ import annotations

from fastapi import Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from agromech_api.security.auth import UserContext, require_roles
from agromech_api.core.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.qa.text import answer_text_question


class TextQaRequest(BaseModel):
    question: str = Field(...)
    filters: dict[str, str | None] = Field(default_factory=dict)
    session_id: str | None = None
    mode: str = "standard"


def register_text_qa_routes(app, *, settings: Settings, engine: Engine) -> None:
    @app.post("/qa/text", tags=["qa"])
    def text_qa(
        payload: TextQaRequest,
        request: Request,
        user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        return answer_text_question(
            engine,
            question=payload.question,
            filters=payload.filters,
            trace_id=request.state.trace_id,
            settings=settings,
            username=user.username,
            session_id=payload.session_id,
        )
