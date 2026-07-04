from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus, TaskType
from agromech_api.db.models import (
    chunk_entity_links,
    document_chunks,
    document_entity_extractions,
    documents,
    metadata,
)
from agromech_api.domain.entities import EntityExtractor, filter_chunks_by_entity, process_document_entities
from agromech_api.ingestion import QueuedTask
from agromech_worker.main import process_ingest_task


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document_with_chunk(engine, content: str, *, metadata_value=None) -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=100,
                mime_type="text/plain",
                storage_uri="file:///tmp/manual.txt",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_chunks).values(
                id="chunk-1",
                document_id="doc-1",
                chunk_type=ChunkType.TEXT.value,
                content=content,
                summary=content[:80],
                source_locator={"type": "text", "line_start": 1, "line_end": 1},
                metadata=metadata_value,
            )
        )


def test_entity_extractor_finds_key_agromech_entities() -> None:
    entities = EntityExtractor().extract(
        "Kubota M7040 hydraulic pump fault code E01 uses part HH-123."
    )

    assert ("brand", "Kubota") in {(entity.entity_type, entity.value) for entity in entities}
    assert ("model", "M7040") in {(entity.entity_type, entity.value) for entity in entities}
    assert ("system", "hydraulic") in {(entity.entity_type, entity.value) for entity in entities}
    assert ("component", "pump") in {(entity.entity_type, entity.value) for entity in entities}
    assert ("fault_code", "E01") in {(entity.entity_type, entity.value) for entity in entities}
    assert ("part_number", "HH-123") in {(entity.entity_type, entity.value) for entity in entities}


def test_entity_extractor_finds_maintenance_items_and_chinese_terms() -> None:
    entities = EntityExtractor().extract(
        "久保田 M-7040 液压系统提升无力，检查液压泵和滤芯；保养项目：更换机油、润滑黄油嘴，配件号 RE-456。"
    )
    values = {(entity.entity_type, entity.value) for entity in entities}

    assert ("brand", "久保田") in values
    assert ("model", "M-7040") in values
    assert ("system", "液压") in values
    assert ("component", "液压泵") in values
    assert ("component", "滤芯") in values
    assert ("maintenance_item", "更换机油") in values
    assert ("maintenance_item", "润滑黄油嘴") in values
    assert ("part_number", "RE-456") in values


def test_process_document_entities_links_entities_to_source_chunks(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunk(
        engine,
        "Kubota M7040 hydraulic pump fault code E01 uses part HH-123.",
    )

    result = process_document_entities(engine, "doc-1")

    assert result.link_count >= 6
    assert result.low_confidence is False
    with engine.connect() as connection:
        links = connection.execute(select(chunk_entity_links)).mappings().all()
        extraction = connection.execute(select(document_entity_extractions)).mappings().one()
    assert {link["chunk_id"] for link in links} == {"chunk-1"}
    assert ("fault_code", "E01") in {(link["entity_type"], link["entity_value"]) for link in links}
    assert extraction["low_confidence"] is False
    assert extraction["extracted_entities"]["model"] == ["M7040"]


def test_process_document_entities_persists_maintenance_and_confidence(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunk(
        engine,
        "Kubota M7040 engine maintenance item: change engine oil and grease fittings with part RE-456.",
    )

    result = process_document_entities(engine, "doc-1")

    assert result.link_count >= 5
    with engine.connect() as connection:
        links = connection.execute(select(chunk_entity_links)).mappings().all()
    maintenance_links = [link for link in links if link["entity_type"] == "maintenance_item"]
    assert {link["entity_value"] for link in maintenance_links} == {
        "change engine oil",
        "grease fittings",
    }
    assert all(link["source"] == "rule" for link in maintenance_links)
    assert all(link["confidence"] >= 0.7 for link in maintenance_links)


def test_no_entities_records_low_confidence_empty_result(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunk(engine, "Routine notes without machine identifiers.")

    result = process_document_entities(engine, "doc-1")

    assert result.link_count == 0
    assert result.low_confidence is True
    with engine.connect() as connection:
        links = connection.execute(select(chunk_entity_links)).mappings().all()
        extraction = connection.execute(select(document_entity_extractions)).mappings().one()
    assert links == []
    assert extraction["low_confidence"] is True
    assert extraction["extracted_entities"] == {}


def test_entity_links_support_structured_chunk_filtering(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_chunk(engine, "John Deere 6M engine filter part RE-456.")
    process_document_entities(engine, "doc-1")

    results = filter_chunks_by_entity(engine, entity_type="brand", value="John Deere")

    assert results == [{"chunk_id": "chunk-1", "document_id": "doc-1"}]


def test_worker_runs_entity_extraction_before_indexing(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    source_path = tmp_path / "manual.txt"
    source_path.write_text("Kubota M7040 hydraulic pump fault code E01", encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id="doc-1",
                title="Manual",
                original_file_name="manual.txt",
                file_hash="hash-doc-1",
                file_size_bytes=source_path.stat().st_size,
                mime_type="text/plain",
                storage_uri=f"file://{source_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )

    process_ingest_task(
        engine,
        QueuedTask(
            id="task-1",
            document_id="doc-1",
            task_type=TaskType.INGEST.value,
            attempt_count=0,
            stage="processing",
        ),
    )

    with engine.connect() as connection:
        links = connection.execute(select(chunk_entity_links)).mappings().all()
    assert ("model", "M7040") in {(link["entity_type"], link["entity_value"]) for link in links}
