from fastapi import FastAPI

from agromech_api.config import get_settings
from agromech_api.infrastructure import DependencyCheck, check_infrastructure


def create_app(dependency_checker=None) -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="AgroMech RAG API", version="0.1.0")
    checker = dependency_checker or (lambda: check_infrastructure(settings))

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
