from __future__ import annotations

from fastapi import Depends, File, Form, Request, UploadFile
from sqlalchemy import Engine

from agromech_api.security.auth import UserContext, require_roles
from agromech_api.core.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.qa.image import answer_image_question


def register_image_qa_routes(app, *, settings: Settings, engine: Engine) -> None:
    @app.post("/qa/image", tags=["qa"])
    async def image_qa(
        request: Request,
        image: list[UploadFile] = File(...),
        question: str | None = Form(default=None),
        brand: str | None = Form(default=None),
        model: str | None = Form(default=None),
        document_type: str | None = Form(default=None),
        language: str | None = Form(default=None),
        session_id: str | None = Form(default=None),
        user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        return await answer_image_question(
            engine,
            settings,
            images=image,
            question=question,
            brand=brand,
            model=model,
            document_type=document_type,
            language=language,
            trace_id=request.state.trace_id,
            username=user.username,
            session_id=session_id,
        )
