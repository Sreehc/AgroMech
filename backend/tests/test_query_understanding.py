from sqlalchemy import create_engine, insert

from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import chunk_entity_links, document_chunks, documents, metadata
from agromech_api.query_understanding import parse_query, structured_filter_chunks


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_model_chunks(engine) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents),
            [
                {
                    "id": "doc-m7040",
                    "title": "M7040 Manual",
                    "original_file_name": "m7040.txt",
                    "file_hash": "hash-m7040",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/m7040.txt",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
                {
                    "id": "doc-l3901",
                    "title": "L3901 Manual",
                    "original_file_name": "l3901.txt",
                    "file_hash": "hash-l3901",
                    "file_size_bytes": 100,
                    "mime_type": "text/plain",
                    "storage_uri": "file:///tmp/l3901.txt",
                    "status": DocumentStatus.INDEXED.value,
                    "created_by_role": "admin",
                },
            ],
        )
        connection.execute(
            insert(document_chunks),
            [
                {
                    "id": "chunk-m7040",
                    "document_id": "doc-m7040",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota M7040 E01 hydraulic pump repair",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-l3901",
                    "document_id": "doc-l3901",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota L3901 E01 electrical sensor repair",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
            ],
        )
        connection.execute(
            insert(chunk_entity_links),
            [
                {
                    "id": "m-model",
                    "chunk_id": "chunk-m7040",
                    "document_id": "doc-m7040",
                    "entity_type": "model",
                    "entity_value": "M7040",
                    "normalized_value": "m7040",
                    "confidence": 0.8,
                    "source": "rule",
                },
                {
                    "id": "m-fault",
                    "chunk_id": "chunk-m7040",
                    "document_id": "doc-m7040",
                    "entity_type": "fault_code",
                    "entity_value": "E01",
                    "normalized_value": "e01",
                    "confidence": 0.86,
                    "source": "rule",
                },
                {
                    "id": "l-model",
                    "chunk_id": "chunk-l3901",
                    "document_id": "doc-l3901",
                    "entity_type": "model",
                    "entity_value": "L3901",
                    "normalized_value": "l3901",
                    "confidence": 0.8,
                    "source": "rule",
                },
                {
                    "id": "l-fault",
                    "chunk_id": "chunk-l3901",
                    "document_id": "doc-l3901",
                    "entity_type": "fault_code",
                    "entity_value": "E01",
                    "normalized_value": "e01",
                    "confidence": 0.86,
                    "source": "rule",
                },
            ],
        )


def test_parse_query_extracts_intent_entities_alias_and_safety() -> None:
    parsed = parse_query("Kubota M-7040 液压 pump 故障码 E01 怎么修？")

    assert parsed.intent == "repair"
    assert parsed.filters["brand"] == "Kubota"
    assert parsed.filters["model"] == "M7040"
    assert parsed.filters["subsystem"] == "hydraulic"
    assert parsed.entities["fault_code"] == ["E01"]
    assert parsed.entities["component"] == ["pump"]
    assert parsed.safety_sensitive is True
    assert parsed.scope_uncertain is False


def test_fault_code_without_model_marks_scope_uncertain() -> None:
    parsed = parse_query("E01 是什么意思？")

    assert parsed.entities["fault_code"] == ["E01"]
    assert parsed.filters.get("model") is None
    assert parsed.scope_uncertain is True
    assert "model" in parsed.needs_clarification


def test_multi_model_query_is_marked_for_separate_handling() -> None:
    parsed = parse_query("M7040 和 L3901 的 E01 怎么处理？")

    assert parsed.filters["models"] == ["M7040", "L3901"]
    assert parsed.multi_model is True
    assert parsed.scope_uncertain is True


def test_structured_filter_prioritizes_explicit_model_chunks(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_model_chunks(engine)
    parsed = parse_query("M7040 E01 怎么修？")

    results = structured_filter_chunks(engine, parsed)

    assert results == [{"chunk_id": "chunk-m7040", "document_id": "doc-m7040", "matched_filters": ["model", "fault_code"]}]


def test_structured_filter_without_model_returns_fault_code_matches_as_uncertain(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_model_chunks(engine)
    parsed = parse_query("E01 怎么修？")

    results = structured_filter_chunks(engine, parsed)

    assert {result["chunk_id"] for result in results} == {"chunk-m7040", "chunk-l3901"}
    assert all(result["scope_uncertain"] is True for result in results)
