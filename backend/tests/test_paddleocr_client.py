import json

import pytest

from agromech_api.core.config import Settings
from agromech_api.integrations.ocr.paddleocr import (
    OcrApiError,
    PaddleOcrApiClient,
    build_paddleocr_client,
)


def ocr_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "vector_backend": "local",
        "model_provider": "local",
        "embedding_provider": "local",
        "paddleocr_api_token": "test-token",
        "paddleocr_poll_interval_seconds": 0.0,
        "paddleocr_poll_timeout_seconds": 5.0,
    }
    base.update(overrides)
    return Settings(**base)


# A real single-page response shape captured from the live API, trimmed to the
# fields the client parses. Mirrors a page with a title, text, an image block
# (with a cropped image file) and a table block whose content is HTML.
def sample_jsonl() -> str:
    line = {
        "result": {
            "layoutParsingResults": [
                {
                    "markdown": {
                        "text": "## GM80\n\n<table><tr><td>厂名</td><td>雷沃</td></tr></table>",
                        "images": {
                            "imgs/img_in_image_box_260_253_960_685.jpg": "https://cdn.example/crop1.jpg"
                        },
                    },
                    "prunedResult": {
                        "width": 1200,
                        "height": 1600,
                        "parsing_res_list": [
                            {
                                "block_label": "paragraph_title",
                                "block_content": "GM80 电控液压检修指导",
                                "block_bbox": [460, 94, 795, 129],
                                "block_id": 0,
                                "block_order": 1,
                                "group_id": 0,
                            },
                            {
                                "block_label": "image",
                                "block_content": "",
                                "block_bbox": [260, 253, 960, 685],
                                "block_id": 1,
                                "block_order": None,
                                "group_id": 1,
                            },
                            {
                                "block_label": "table",
                                "block_content": "<table><tr><td>厂名</td><td>雷沃</td></tr></table>",
                                "block_bbox": [128, 173, 1095, 707],
                                "block_id": 2,
                                "block_order": 2,
                                "group_id": 2,
                            },
                        ],
                    },
                }
            ]
        }
    }
    return json.dumps(line, ensure_ascii=False)


class ScriptedTransport:
    """Replays a fixed sequence of job states without touching the network."""

    def __init__(self, states: list[str], *, submit_status: int = 200):
        self._states = list(states)
        self._submit_status = submit_status
        self.requests: list[tuple[str, str]] = []

    def request(self, method, url, *, headers, body, timeout):
        self.requests.append((method, url))
        if method == "POST":
            return self._submit_status, json.dumps({"data": {"jobId": "job-123"}}).encode()
        if url.endswith("/crop1.jpg") or url.startswith("https://cdn") or url.endswith(".json"):
            pass
        if url.endswith("result.jsonl"):
            return 200, sample_jsonl().encode("utf-8")
        # Poll endpoint: advance through the scripted states.
        state = self._states.pop(0) if self._states else "done"
        data: dict = {"state": state}
        if state == "done":
            data["resultUrl"] = {"jsonUrl": "https://cdn.example/result.jsonl"}
        if state == "failed":
            data["errorMsg"] = "boom"
        return 200, json.dumps({"data": data}).encode()


def test_parse_document_returns_pages_with_regions() -> None:
    transport = ScriptedTransport(["pending", "running", "done"])
    client = PaddleOcrApiClient(ocr_settings(), transport=transport)

    result = client.parse_document(content=b"%PDF-1.4", filename="manual.pdf", sleep=lambda _s: None)

    assert len(result.pages) == 1
    page = result.pages[0]
    assert page.width == 1200
    assert "GM80" in page.markdown
    assert [region.region_type for region in page.regions] == ["paragraph_title", "image", "table"]


def test_table_region_preserves_html() -> None:
    transport = ScriptedTransport(["done"])
    client = PaddleOcrApiClient(ocr_settings(), transport=transport)

    result = client.parse_document(content=b"%PDF", filename="m.pdf", sleep=lambda _s: None)

    table = next(r for r in result.pages[0].regions if r.region_type == "table")
    assert table.text.startswith("<table>")
    assert "雷沃" in table.text


def test_image_region_links_to_cropped_image() -> None:
    transport = ScriptedTransport(["done"])
    client = PaddleOcrApiClient(ocr_settings(), transport=transport)

    result = client.parse_document(content=b"%PDF", filename="m.pdf", sleep=lambda _s: None)

    image = next(r for r in result.pages[0].regions if r.region_type == "image")
    assert image.image_path == "imgs/img_in_image_box_260_253_960_685.jpg"
    assert image.image_url == "https://cdn.example/crop1.jpg"


def test_missing_token_raises() -> None:
    client = PaddleOcrApiClient(ocr_settings(paddleocr_api_token=""), transport=ScriptedTransport(["done"]))

    with pytest.raises(OcrApiError, match="PADDLEOCR_API_TOKEN"):
        client.parse_document(content=b"x", filename="m.pdf", sleep=lambda _s: None)


def test_failed_job_raises() -> None:
    transport = ScriptedTransport(["running", "failed"])
    client = PaddleOcrApiClient(ocr_settings(), transport=transport)

    with pytest.raises(OcrApiError, match="boom"):
        client.parse_document(content=b"x", filename="m.pdf", sleep=lambda _s: None)


def test_submit_http_error_raises() -> None:
    transport = ScriptedTransport(["done"], submit_status=500)
    client = PaddleOcrApiClient(ocr_settings(), transport=transport)

    with pytest.raises(OcrApiError, match="HTTP 500"):
        client.parse_document(content=b"x", filename="m.pdf", sleep=lambda _s: None)


def test_poll_timeout_raises() -> None:
    transport = ScriptedTransport(["pending", "pending", "pending"])
    client = PaddleOcrApiClient(ocr_settings(), transport=transport)

    # now() jumps past the deadline on the second check so the loop gives up.
    ticks = iter([0.0, 0.0, 100.0, 100.0])

    with pytest.raises(OcrApiError, match="timed out"):
        client.parse_document(
            content=b"x",
            filename="m.pdf",
            sleep=lambda _s: None,
            now=lambda: next(ticks),
        )


def test_build_paddleocr_client() -> None:
    client = build_paddleocr_client(ocr_settings(), transport=ScriptedTransport(["done"]))

    assert isinstance(client, PaddleOcrApiClient)
