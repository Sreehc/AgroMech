from __future__ import annotations

from uuid import uuid4

from sqlalchemy import Engine, insert

from agromech_api.db.models import answer_citations, qa_records


def record_qa(engine: Engine, *, question: str, payload: dict[str, object]) -> None:
    qa_record_id = str(uuid4())
    with engine.begin() as connection:
        connection.execute(
            insert(qa_records).values(
                id=qa_record_id,
                trace_id=payload["trace_id"],
                question=question,
                answer=payload["answer"],
                sections=payload["sections"],
                uncertainty=payload["uncertainty"],
            )
        )
        for citation in payload["citations"]:
            connection.execute(
                insert(answer_citations).values(
                    id=str(uuid4()),
                    qa_record_id=qa_record_id,
                    document_id=citation["document_id"],
                    chunk_id=citation.get("chunk_id"),
                    asset_id=citation.get("asset_id"),
                    page_number=citation.get("page_number"),
                    citation_payload=citation,
                    accessible=citation["accessible"],
                )
            )

