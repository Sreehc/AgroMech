from uuid import uuid4

from fastapi import FastAPI, Request
from sqlalchemy import Engine

from agromech_api.api.auth import register_auth_routes
from agromech_api.api.health import register_health_routes
from agromech_api.security.auth import require_authenticated_write
from agromech_api.sessions.routes import register_chat_session_routes
from agromech_api.core.config import Settings, get_settings
from agromech_api.core.database import get_engine
from agromech_api.documents.routes import register_document_routes
from agromech_api.core.errors import register_error_handlers
from agromech_api.qa.image_routes import register_image_qa_routes
from agromech_api.core.infrastructure import check_infrastructure
from agromech_api.rag.traces import register_retrieval_trace_routes
from agromech_api.integrations.queue.task_queue import build_task_publisher
from agromech_api.qa.text_routes import register_text_qa_routes


def create_app(
    dependency_checker=None,
    settings: Settings | None = None,
    database_engine: Engine | None = None,
    task_publisher=None,
) -> FastAPI:
    settings = settings or get_settings()
    database_engine = database_engine or get_engine()
    app = FastAPI(title="AgroMech RAG API", version="0.1.0")
    app.state.settings = settings
    app.state.database_engine = database_engine
    register_error_handlers(app)
    task_publisher = task_publisher or build_task_publisher(settings)
    register_document_routes(app, settings=settings, engine=database_engine, task_publisher=task_publisher)
    register_chat_session_routes(app, engine=database_engine)
    register_retrieval_trace_routes(app, engine=database_engine)
    register_text_qa_routes(app, settings=settings, engine=database_engine)
    register_image_qa_routes(app, settings=settings, engine=database_engine)
    checker = dependency_checker or (lambda: check_infrastructure(settings))

    @app.middleware("http")
    async def attach_trace_id(request: Request, call_next):
        request.state.trace_id = request.headers.get("X-Trace-Id") or str(uuid4())
        response = await call_next(request)
        response.headers["X-Trace-Id"] = request.state.trace_id
        return response

    app.middleware("http")(require_authenticated_write)
    register_auth_routes(app, settings=settings, engine=database_engine)
    register_health_routes(app, settings=settings, dependency_checker=checker)

    return app


app = create_app()
