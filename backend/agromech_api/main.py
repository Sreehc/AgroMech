from fastapi import FastAPI

from agromech_api.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AgroMech RAG API", version="0.1.0")

    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "api",
            "environment": settings.app_env,
        }

    return app


app = create_app()

