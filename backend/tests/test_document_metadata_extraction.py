import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import ChunkType, DocumentStatus
from agromech_api.db.models import document_chunks, documents, metadata
from agromech_api.document_metadata_extraction import (
    MetadataExtractionError,
    backfill_document_metadata,
    parse_metadata_response,
)


def create_test_engine(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document(engine, *, brand=None, model=None) -> None:
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
                brand=brand,
                model=model,
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )
        connection.execute(
            insert(document_chunks).values(
                id="chunk-1",
                document_id="doc-1",
                chunk_type=ChunkType.TEXT.value,
                content="雷沃欧豹MG系列轮式拖拉机MG2004说明书",
                source_locator={"type": "text", "line_start": 1, "line_end": 1},
            )
        )


class FakeExtractor:
    def __init__(self, payload):
        self.payload = payload

    def extract(self, _context):
        return self.payload


def test_backfill_document_metadata_does_not_overwrite_existing_fields(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine, brand="用户品牌", model="用户型号")

    result = backfill_document_metadata(
        engine,
        "doc-1",
        extractor=FakeExtractor(
            {
                "brand": "雷沃欧豹",
                "model": "MG2004",
                "document_type": "说明书",
                "language": "zh-CN",
                "source": "upload",
                "confidence": 0.9,
            }
        ),
    )

    with engine.connect() as connection:
        document = connection.execute(select(documents).where(documents.c.id == "doc-1")).mappings().one()
    assert document["brand"] == "用户品牌"
    assert document["model"] == "用户型号"
    assert document["document_type"] == "说明书"
    assert result.updated_fields == {
        "document_type": "说明书",
        "language": "zh-CN",
        "source": "upload",
    }


def test_backfill_document_metadata_skips_low_confidence_payload(tmp_path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    result = backfill_document_metadata(
        engine,
        "doc-1",
        extractor=FakeExtractor({"brand": "雷沃欧豹", "model": "MG2004", "confidence": 0.2}),
    )

    with engine.connect() as connection:
        document = connection.execute(select(documents).where(documents.c.id == "doc-1")).mappings().one()
    assert result.skipped is True
    assert document["brand"] is None
    assert document["model"] is None


def test_parse_metadata_response_rejects_invalid_llm_json() -> None:
    with pytest.raises(MetadataExtractionError, match="not valid JSON"):
        parse_metadata_response({"choices": [{"message": {"content": "not-json"}}]})
