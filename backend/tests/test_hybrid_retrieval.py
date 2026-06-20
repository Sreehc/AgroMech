from sqlalchemy import create_engine, insert

from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import document_chunks, documents, metadata
from agromech_api.entity_extraction import process_document_entities
from agromech_api.graph_rag import GraphRagService
from agromech_api.hybrid_retrieval import hybrid_retrieve
from agromech_api.search_indexing import SearchIndexer


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_retrieval_corpus(engine) -> None:
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
                {
                    "id": "doc-image",
                    "title": "Image Observation",
                    "original_file_name": "warning.png",
                    "file_hash": "hash-image",
                    "file_size_bytes": 100,
                    "mime_type": "image/png",
                    "storage_uri": "file:///tmp/warning.png",
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
                    "content": "Kubota M7040 hydraulic pump fault code E01 check pump pressure.",
                    "summary": "M7040 E01 hydraulic pump",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-l3901",
                    "document_id": "doc-l3901",
                    "chunk_type": ChunkType.TEXT.value,
                    "content": "Kubota L3901 fault code E01 electrical sensor troubleshooting.",
                    "summary": "L3901 E01 electrical sensor",
                    "source_locator": {"type": "text", "line_start": 1, "line_end": 1},
                },
                {
                    "id": "chunk-image",
                    "document_id": "doc-image",
                    "chunk_type": ChunkType.IMAGE.value,
                    "content": "Visual description: dashboard hydraulic warning light with E01.",
                    "summary": "dashboard hydraulic warning light",
                    "source_locator": {"type": "image", "source_file": "warning.png"},
                    "metadata": {"detected_entities": {"possible_models": ["M7040"], "warning_lights": ["hydraulic"]}},
                },
            ],
        )
    for document_id in ["doc-m7040", "doc-l3901", "doc-image"]:
        process_document_entities(engine, document_id)
        GraphRagService(engine).sync_document(document_id)
        SearchIndexer(engine).index_document(document_id)


def test_hybrid_retrieval_merges_channels_and_deduplicates_candidates(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(engine, "M7040 E01 hydraulic pump")

    assert result["status"] == "ok"
    m7040 = next(candidate for candidate in result["candidates"] if candidate["chunk_id"] == "chunk-m7040")
    assert set(m7040["channels"]) >= {"keyword", "vector", "structured", "graph"}
    assert len([candidate for candidate in result["candidates"] if candidate["chunk_id"] == "chunk-m7040"]) == 1


def test_hybrid_retrieval_marks_unrelated_model_candidates_not_applicable(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(engine, "M7040 E01 repair")

    first = result["candidates"][0]
    unrelated = next(candidate for candidate in result["candidates"] if candidate["chunk_id"] == "chunk-l3901")
    assert first["chunk_id"] == "chunk-m7040"
    assert unrelated["not_applicable"] is True
    assert unrelated["applicability_reason"] == "model_mismatch"
    assert unrelated["score"] < first["score"]


def test_hybrid_retrieval_includes_vision_channel_for_image_candidates(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(engine, "dashboard hydraulic warning image")

    image_candidate = next(candidate for candidate in result["candidates"] if candidate["chunk_id"] == "chunk-image")
    assert "vision" in image_candidate["channels"]
    assert image_candidate["chunk_type"] == ChunkType.IMAGE.value


def test_hybrid_retrieval_returns_evidence_insufficient_when_no_candidates(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_retrieval_corpus(engine)

    result = hybrid_retrieve(engine, "orchard sprayer calibration nozzle")

    assert result == {
        "status": "evidence_insufficient",
        "candidates": [],
        "message": "No evidence found for the query",
    }
