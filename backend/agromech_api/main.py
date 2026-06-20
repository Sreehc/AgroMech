from uuid import uuid4

from fastapi import Depends, FastAPI, Request
from pydantic import BaseModel
from sqlalchemy import Engine

from agromech_api.auth import (
    UserContext,
    authenticate_single_admin,
    create_access_token,
    current_user_dependency,
    require_authenticated_write,
)
from agromech_api.config import Settings, get_settings
from agromech_api.database import get_engine
from agromech_api.documents import register_document_routes
from agromech_api.errors import register_error_handlers
from agromech_api.infrastructure import DependencyCheck, check_infrastructure
from agromech_api.retrieval_traces import register_retrieval_trace_routes
from agromech_api.text_qa import register_text_qa_routes


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int


def create_app(
    dependency_checker=None,
    settings: Settings | None = None,
    database_engine: Engine | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    database_engine = database_engine or get_engine()
    app = FastAPI(title="AgroMech RAG API", version="0.1.0")
    app.state.settings = settings
    app.state.database_engine = database_engine
    register_error_handlers(app)
    register_document_routes(app, settings=settings, engine=database_engine)
    register_retrieval_trace_routes(app, engine=database_engine)
    register_text_qa_routes(app, engine=database_engine)
    checker = dependency_checker or (lambda: check_infrastructure(settings))

    @app.middleware("http")
    async def attach_trace_id(request: Request, call_next):
        request.state.trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    app.middleware("http")(require_authenticated_write)

    @app.post("/auth/login", response_model=LoginResponse, tags=["auth"])
    def login(payload: LoginRequest) -> LoginResponse:
        user = authenticate_single_admin(payload.username, payload.password, settings)
        token = create_access_token(username=user.username, role=user.role, settings=settings)
        return LoginResponse(
            access_token=token,
            expires_in=settings.session_ttl_minutes * 60,
        )

    @app.get("/auth/me", tags=["auth"])
    def me(user: UserContext = Depends(current_user_dependency())) -> dict[str, str]:
        return {"username": user.username, "role": user.role.value}

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "api",
            "environment": settings.app_env,
        }

    @app.get("/health/dependencies", tags=["system"])
    def dependency_health() -> dict[str, object]:
        checks: list[DependencyCheck] = checker()
        status = "ok" if all(check.status == "ok" for check in checks) else "degraded"
        return {
            "status": status,
            "dependencies": [check.to_dict() for check in checks],
        }

    return app


app = create_app()
