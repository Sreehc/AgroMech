from __future__ import annotations

from fastapi import Depends, status
from sqlalchemy import Engine, select

from agromech_api.auth import UserContext, require_roles
from agromech_api.db.enums import UserRole
from agromech_api.db.models import retrieval_logs
from agromech_api.errors import AppError, ErrorCode


FULL_TRACE_ROLES = {UserRole.ADMIN, UserRole.EVALUATOR}


def retrieval_trace_payload(row, user: UserContext) -> dict[str, object]:
    payload = {
        "trace_id": row["trace_id"],
        "query": row["query"],
        "filters": row["filters"] or {},
        "channels": row["channels"] or {"used": [], "degraded": []},
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
        return payload

    payload["final_evidence"] = [
        {"chunk_id": evidence["chunk_id"]}
        for evidence in row["final_evidence"] or []
        if evidence.get("chunk_id")
    ]
    return payload


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
