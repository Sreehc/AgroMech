from __future__ import annotations

from collections.abc import Callable

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
