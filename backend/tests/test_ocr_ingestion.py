from pathlib import Path

import pytest
from sqlalchemy import create_engine, insert, select

from agromech_api.db.enums import AssetType, ChunkType, DocumentStatus
from agromech_api.db.models import document_assets, document_chunks, documents, metadata
from agromech_api.ingestion import IngestFailure
from agromech_api.ingestion.ocr import OcrIngestionResult, process_ocr_document
from agromech_api.integrations.ocr.paddleocr import OcrPage, OcrRegion, OcrResult


def create_test_engine(tmp_path: Path):
    engine = create_engine(f"sqlite:///{tmp_path / 'agromech.db'}")
    metadata.create_all(engine)
    return engine


def seed_document(engine, *, document_id: str = "doc-1") -> None:
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id=document_id,
                title="MG2004 manual",
                original_file_name="mg2004.pdf",
                file_hash="hash-1",
                file_size_bytes=1024,
                mime_type="application/pdf",
                storage_uri="file:///tmp/mg2004.pdf",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )


def ocr_result_two_pages() -> OcrResult:
    return OcrResult(
        pages=[
            OcrPage(page_index=0, markdown="# 产品识别标志记录表\n制造厂电话 4006589888"),
            OcrPage(page_index=1, markdown="第二页内容：液压油每 400 小时更换"),
        ]
    )


def fetch_text_chunks(engine, document_id: str = "doc-1"):
    with engine.connect() as connection:
        return connection.execute(
            select(document_chunks)
            .where(document_chunks.c.document_id == document_id)
            .where(document_chunks.c.chunk_type == ChunkType.TEXT.value)
            .order_by(document_chunks.c.page_number)
        ).mappings().all()


def test_process_ocr_document_creates_one_text_chunk_per_page(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    result = process_ocr_document(engine, "doc-1", ocr_result=ocr_result_two_pages())

    assert isinstance(result, OcrIngestionResult)
    assert result.page_count == 2
    assert result.text_chunk_count == 2

    chunks = fetch_text_chunks(engine)
    assert [chunk["page_number"] for chunk in chunks] == [1, 2]
    assert "制造厂电话 4006589888" in chunks[0]["content"]
    assert chunks[0]["source_locator"] == {"type": "ocr_text", "page": 1}


def test_table_text_is_captured_as_plain_text(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    # A page whose only text comes from layout blocks (table cells as text).
    page = OcrPage(
        page_index=0,
        markdown="",
        regions=[
            OcrRegion(region_type="table", bbox=[0, 0, 10, 10], text="制造厂名称 雷沃重工"),
            OcrRegion(region_type="text", bbox=[0, 20, 10, 30], text="联系电话 4006589888"),
        ],
    )

    result = process_ocr_document(engine, "doc-1", ocr_result=OcrResult(pages=[page]))

    assert result.text_chunk_count == 1
    chunks = fetch_text_chunks(engine)
    assert "制造厂名称 雷沃重工" in chunks[0]["content"]
    assert "联系电话 4006589888" in chunks[0]["content"]


def test_blank_pages_are_skipped(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    ocr_result = OcrResult(
        pages=[
            OcrPage(page_index=0, markdown="   "),
            OcrPage(page_index=1, markdown="有内容的一页"),
        ]
    )

    result = process_ocr_document(engine, "doc-1", ocr_result=ocr_result)

    assert result.page_count == 2
    assert result.text_chunk_count == 1
    chunks = fetch_text_chunks(engine)
    assert chunks[0]["page_number"] == 2


def test_no_pages_raises_ingest_failure(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    with pytest.raises(IngestFailure) as exc:
        process_ocr_document(engine, "doc-1", ocr_result=OcrResult(pages=[]))

    assert exc.value.code == "ocr_no_pages"
    assert exc.value.stage == "ocr"


def test_all_blank_pages_raises_ingest_failure(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    ocr_result = OcrResult(pages=[OcrPage(page_index=0, markdown="")])

    with pytest.raises(IngestFailure) as exc:
        process_ocr_document(engine, "doc-1", ocr_result=ocr_result)

    assert exc.value.code == "ocr_no_text_extracted"
    assert exc.value.stage == "ocr"


def test_reprocessing_replaces_existing_text_chunks(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document(engine)

    process_ocr_document(engine, "doc-1", ocr_result=ocr_result_two_pages())
    # Second run with a single page should leave exactly one chunk.
    result = process_ocr_document(
        engine,
        "doc-1",
        ocr_result=OcrResult(pages=[OcrPage(page_index=0, markdown="替换后的内容")]),
    )

    assert result.text_chunk_count == 1
    chunks = fetch_text_chunks(engine)
    assert len(chunks) == 1
    assert chunks[0]["content"] == "替换后的内容"


def seed_document_with_real_pdf(engine, tmp_path: Path, *, document_id: str = "doc-1") -> Path:
    """Seed a document backed by a real one-page PDF so page rendering works."""
    import fitz

    pdf_path = tmp_path / "doc.pdf"
    doc = fitz.open()
    doc.new_page()
    doc.save(pdf_path)
    doc.close()
    with engine.begin() as connection:
        connection.execute(
            insert(documents).values(
                id=document_id,
                title="GM80 hydraulics",
                original_file_name="gm80.pdf",
                file_hash="hash-pdf",
                file_size_bytes=pdf_path.stat().st_size,
                mime_type="application/pdf",
                storage_uri=f"file://{pdf_path}",
                status=DocumentStatus.PROCESSING.value,
                created_by_role="admin",
            )
        )
    return pdf_path


def fetch_assets(engine, asset_type: str, document_id: str = "doc-1"):
    with engine.connect() as connection:
        return connection.execute(
            select(document_assets)
            .where(document_assets.c.document_id == document_id)
            .where(document_assets.c.asset_type == asset_type)
            .order_by(document_assets.c.page_number)
        ).mappings().all()


def fetch_table_chunks(engine, document_id: str = "doc-1"):
    with engine.connect() as connection:
        return connection.execute(
            select(document_chunks)
            .where(document_chunks.c.document_id == document_id)
            .where(document_chunks.c.chunk_type == ChunkType.TABLE.value)
        ).mappings().all()


def test_table_block_is_persisted_as_table_chunk_with_html(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_real_pdf(engine, tmp_path)

    table_html = "<table><tr><td>制造厂名称</td><td>雷沃重工股份有限公司</td></tr></table>"
    page = OcrPage(
        page_index=0,
        markdown="# 产品识别标志记录表",
        regions=[
            OcrRegion(region_type="table", bbox=[10, 20, 300, 400], text=table_html),
        ],
    )

    result = process_ocr_document(
        engine,
        "doc-1",
        ocr_result=OcrResult(pages=[page]),
        persist_visual=True,
        asset_root=tmp_path,
    )

    assert result.table_chunk_count == 1
    tables = fetch_table_chunks(engine)
    assert len(tables) == 1
    # The full <table> HTML is preserved verbatim, not flattened to text.
    assert tables[0]["content"] == table_html
    assert tables[0]["source_locator"]["type"] == "ocr_table"
    assert tables[0]["source_locator"]["bbox"] == [10, 20, 300, 400]


def test_region_crops_persisted_as_extracted_images_linked_to_page(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_real_pdf(engine, tmp_path)

    page = OcrPage(
        page_index=0,
        markdown="液压系统检修示意",
        width=960,
        height=720,
        regions=[
            OcrRegion(
                region_type="image",
                bbox=[260, 253, 960, 685],
                text="",
                image_path="imgs/img_in_image_box_260_253_960_685.jpg",
                image_url="https://example.test/crop.jpg",
            ),
        ],
    )

    fetched: list[str] = []

    def fake_fetcher(url: str) -> bytes:
        fetched.append(url)
        return b"\xff\xd8\xff\xe0fake-jpeg-bytes"

    result = process_ocr_document(
        engine,
        "doc-1",
        ocr_result=OcrResult(pages=[page]),
        persist_visual=True,
        asset_root=tmp_path,
        region_image_fetcher=fake_fetcher,
    )

    assert fetched == ["https://example.test/crop.jpg"]
    assert result.page_asset_count == 1
    assert result.region_asset_count == 1

    page_assets = fetch_assets(engine, AssetType.PAGE_IMAGE.value)
    region_assets = fetch_assets(engine, AssetType.EXTRACTED_IMAGE.value)
    assert len(page_assets) == 1
    assert len(region_assets) == 1

    region = region_assets[0]
    parent = region["source_locator"]["parent"]
    # The region points back at its parent page (page↔region parent/child).
    assert parent["asset_id"] == page_assets[0]["id"]
    assert parent["asset_type"] == AssetType.PAGE_IMAGE.value
    assert region["source_locator"]["region_type"] == "image"
    assert region["source_locator"]["bbox"] == [260, 253, 960, 685]
    # The crop bytes were written to local storage.
    region_path = Path(region["storage_uri"].removeprefix("file://"))
    assert region_path.exists()


def test_visual_persistence_reruns_replace_prior_assets(tmp_path: Path) -> None:
    engine = create_test_engine(tmp_path)
    seed_document_with_real_pdf(engine, tmp_path)

    page = OcrPage(page_index=0, markdown="第一页", regions=[])
    process_ocr_document(
        engine, "doc-1", ocr_result=OcrResult(pages=[page]), persist_visual=True, asset_root=tmp_path
    )
    process_ocr_document(
        engine, "doc-1", ocr_result=OcrResult(pages=[page]), persist_visual=True, asset_root=tmp_path
    )

    # Re-running leaves exactly one page asset, not duplicates.
    assert len(fetch_assets(engine, AssetType.PAGE_IMAGE.value)) == 1
