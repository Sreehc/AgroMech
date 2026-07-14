from __future__ import annotations

from collections.abc import Callable

from fastapi import Response, status

from agromech_api.core.config import Settings
from agromech_api.core.infrastructure import DependencyCheck


def register_health_routes(
    app,
    *,
    settings: Settings,
    dependency_checker: Callable[[], list[DependencyCheck]],
) -> None:
    @app.get("/health", tags=["system"])
    def health() -> dict[str, str]:
        return {
            "status": "ok",
            "service": "api",
            "environment": settings.app_env,
        }

    @app.get("/health/dependencies", tags=["system"])
    def dependency_health() -> dict[str, object]:
        checks = dependency_checker()
        status = "ok" if all(check.status == "ok" for check in checks) else "degraded"
        return {
            "status": status,
            "dependencies": [check.to_dict() for check in checks],
        }

    @app.get("/health/ready", tags=["system"])
    def readiness(response: Response) -> dict[str, object]:
        checks = dependency_checker()
        ready = all(check.status == "ok" for check in checks)
        if not ready:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return {
            "status": "ok" if ready else "unavailable",
            "dependencies": [check.to_dict() for check in checks],
        }
