from __future__ import annotations

from fastapi import Depends, status
from sqlalchemy import Engine, select

from agromech_api.auth import UserContext, require_roles
from agromech_api.db.enums import UserRole
from agromech_api.db.models import retrieval_logs
from agromech_api.errors import AppError, ErrorCode


FULL_TRACE_ROLES = {UserRole.ADMIN, UserRole.EVALUATOR}
REDACTED_VALUE = "[redacted]"
SENSITIVE_KEY_TERMS = {
    "api_key",
    "apikey",
    "authorization",
    "credential",
    "password",
    "private_key",
    "secret",
    "stack",
    "token",
    "traceback",
}
SENSITIVE_PATH_KEY_TERMS = {
    "file_path",
    "internal_path",
    "path",
    "source_path",
}
INTERNAL_PATH_MARKERS = (
    "/Users/",
    "/home/",
    "/private/",
    "/srv/",
    "/var/",
    "/etc/",
    "C:\\",
)


def retrieval_trace_payload(row, user: UserContext) -> dict[str, object]:
    payload = {
        "trace_id": row["trace_id"],
        "query": row["query"],
        "filters": row["filters"] or {},
        "channels": row["channels"] or {"used": [], "degraded": []},
        "model_config": row["model_config"] or {},
        "created_at": row["created_at"].isoformat() if row["created_at"] else None,
    }
    if user.role in FULL_TRACE_ROLES:
        payload.update(
            {
                "candidates": row["candidates"] or [],
                "rerank": row["rerank"] or {"items": []},
                "final_evidence": row["final_evidence"] or [],
            }
        )
        return sanitize_trace_payload(payload)

    payload["final_evidence"] = [
        {"chunk_id": evidence["chunk_id"]}
        for evidence in row["final_evidence"] or []
        if evidence.get("chunk_id")
    ]
    return sanitize_trace_payload(payload)


def sanitize_trace_payload(value):
    if isinstance(value, dict):
        return {key: sanitize_trace_value(key, nested_value) for key, nested_value in value.items()}
    if isinstance(value, list):
        return [sanitize_trace_payload(item) for item in value]
    if isinstance(value, str) and contains_sensitive_runtime_detail(value):
        return REDACTED_VALUE
    return value


def sanitize_trace_value(key: str, value):
    normalized_key = key.lower()
    if key_is_sensitive(normalized_key):
        return REDACTED_VALUE
    return sanitize_trace_payload(value)


def key_is_sensitive(normalized_key: str) -> bool:
    return any(term in normalized_key for term in SENSITIVE_KEY_TERMS | SENSITIVE_PATH_KEY_TERMS)


def contains_sensitive_runtime_detail(value: str) -> bool:
    if "Traceback" in value or "\n  File " in value:
        return True
    return any(marker in value for marker in INTERNAL_PATH_MARKERS)


def register_retrieval_trace_routes(app, *, engine: Engine) -> None:
    @app.get("/retrieval-traces/{trace_id}", tags=["retrieval"])
    def get_retrieval_trace(
        trace_id: str,
        user: UserContext = Depends(
            require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)
        ),
    ) -> dict[str, object]:
        with engine.connect() as connection:
            trace = connection.execute(
                select(retrieval_logs).where(retrieval_logs.c.trace_id == trace_id)
            ).mappings().one_or_none()
        if trace is None:
            raise AppError(ErrorCode.NOT_FOUND, "Retrieval trace not found", status_code=status.HTTP_404_NOT_FOUND)
        return retrieval_trace_payload(trace, user)
