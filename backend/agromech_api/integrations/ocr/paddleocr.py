from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Protocol

from agromech_api.core.config import Settings


# A transport performs one HTTP request and returns (status_code, body_bytes).
# Keeping the network behind this seam lets the client be exercised offline with
# canned responses, mirroring the embedding adapter's injectable client.
HttpResponse = tuple[int, bytes]


class OcrTransport(Protocol):
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse: ...


class OcrApiError(RuntimeError):
    """Raised when the PaddleOCR cloud API cannot complete a request.

    The ingestion processor translates this into an ``IngestFailure`` at the
    ``ocr`` stage so the document never reaches ``indexed``.
    """


@dataclass(frozen=True)
class OcrRegion:
    """One layout block from ``parsing_res_list``.

    ``region_type`` is the API's ``block_label`` (paragraph_title / text / image
    / table / vision_footnote …). For ``table`` blocks ``text`` holds the full
    ``<table>`` HTML the API returns, so row/column structure is preserved.
    """

    region_type: str
    bbox: list[int]
    text: str
    order: int | None = None
    block_id: int | None = None
    group_id: int | None = None
    image_path: str | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class OcrPage:
    page_index: int
    markdown: str
    regions: list[OcrRegion] = field(default_factory=list)
    images: dict[str, str] = field(default_factory=dict)
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class OcrResult:
    pages: list[OcrPage]


class PaddleOcrApiClient:
    """Adapter for the Baidu AI Studio hosted PaddleOCR cloud API.

    The API is an asynchronous job service: submit a file, poll the job until it
    reaches ``done``/``failed``, then fetch the JSONL result and parse each page
    into structured ``OcrPage`` records (Markdown text, layout regions with bbox
    and type, and cropped region image URLs).
    """

    def __init__(self, settings: Settings, *, transport: OcrTransport | None = None) -> None:
        self._base_url = settings.paddleocr_api_base_url.rstrip("/")
        self._token = settings.paddleocr_api_token
        self._model = settings.paddleocr_api_model
        self._submit_timeout = settings.paddleocr_submit_timeout_seconds
        self._poll_interval = max(0.0, settings.paddleocr_poll_interval_seconds)
        self._poll_timeout = settings.paddleocr_poll_timeout_seconds
        self._transport = transport or _UrllibTransport()

    @property
    def _jobs_url(self) -> str:
        return f"{self._base_url}/api/v2/ocr/jobs"

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"bearer {self._token}"}

    def parse_document(
        self,
        *,
        content: bytes,
        filename: str,
        optional_payload: dict[str, object] | None = None,
        sleep=time.sleep,
        now=time.monotonic,
    ) -> OcrResult:
        """Submit a document, wait for completion, and return parsed pages.

        ``sleep`` and ``now`` are injectable so polling can be driven
        deterministically in tests without real delays.
        """
        if not self._token:
            raise OcrApiError(
                "ocr_provider=paddleocr cloud API requires PADDLEOCR_API_TOKEN to be configured"
            )
        job_id = self._submit_job(content=content, filename=filename, optional_payload=optional_payload)
        json_url = self._poll_until_done(job_id, sleep=sleep, now=now)
        jsonl = self._fetch_text(json_url)
        return _parse_jsonl(jsonl)

    def _submit_job(
        self,
        *,
        content: bytes,
        filename: str,
        optional_payload: dict[str, object] | None,
    ) -> str:
        boundary = f"----agromech{uuid.uuid4().hex}"
        fields = {
            "model": self._model,
            "optionalPayload": json.dumps(optional_payload or {}),
        }
        body = _encode_multipart(boundary, fields, filename, content)
        headers = {
            **self._auth_headers(),
            "Content-Type": f"multipart/form-data; boundary={boundary}",
        }
        status, raw = self._transport.request(
            "POST", self._jobs_url, headers=headers, body=body, timeout=self._submit_timeout
        )
        if status != 200:
            raise OcrApiError(f"OCR submit failed with HTTP {status}")
        payload = _load_json(raw)
        job_id = _dig(payload, "data", "jobId")
        if not job_id:
            raise OcrApiError("OCR submit response missing data.jobId")
        return str(job_id)

    def _poll_until_done(self, job_id: str, *, sleep, now) -> str:
        url = f"{self._jobs_url}/{job_id}"
        deadline = now() + self._poll_timeout
        while True:
            status, raw = self._transport.request(
                "GET", url, headers=self._auth_headers(), body=None, timeout=self._submit_timeout
            )
            if status != 200:
                raise OcrApiError(f"OCR poll failed with HTTP {status}")
            data = _dig(_load_json(raw), "data") or {}
            state = data.get("state")
            if state == "done":
                json_url = _dig(data, "resultUrl", "jsonUrl")
                if not json_url:
                    raise OcrApiError("OCR job done but missing resultUrl.jsonUrl")
                return str(json_url)
            if state == "failed":
                raise OcrApiError(f"OCR job failed: {data.get('errorMsg') or 'unknown error'}")
            if now() >= deadline:
                raise OcrApiError(f"OCR job timed out after {self._poll_timeout}s (last state: {state})")
            if self._poll_interval:
                sleep(self._poll_interval)

    def _fetch_text(self, url: str) -> str:
        status, raw = self._transport.request(
            "GET", url, headers={}, body=None, timeout=self._submit_timeout
        )
        if status != 200:
            raise OcrApiError(f"OCR result fetch failed with HTTP {status}")
        return raw.decode("utf-8")


def _parse_jsonl(jsonl: str) -> OcrResult:
    pages: list[OcrPage] = []
    page_index = 0
    for line in jsonl.strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        result = _dig(_load_json(line), "result") or {}
        for layout in result.get("layoutParsingResults") or []:
            pages.append(_parse_page(layout, page_index))
            page_index += 1
    return OcrResult(pages=pages)


def _parse_page(layout: dict, page_index: int) -> OcrPage:
    markdown_block = layout.get("markdown") or {}
    markdown_text = str(markdown_block.get("text") or "")
    images = {
        str(path): str(url)
        for path, url in (markdown_block.get("images") or {}).items()
    }
    pruned = layout.get("prunedResult") or {}
    regions = [
        _parse_region(block, images)
        for block in (pruned.get("parsing_res_list") or [])
    ]
    return OcrPage(
        page_index=page_index,
        markdown=markdown_text,
        regions=regions,
        images=images,
        width=_as_int(pruned.get("width")),
        height=_as_int(pruned.get("height")),
    )


def _parse_region(block: dict, images: dict[str, str]) -> OcrRegion:
    bbox = [int(value) for value in (block.get("block_bbox") or [])]
    image_path = _match_region_image(bbox, images)
    return OcrRegion(
        region_type=str(block.get("block_label") or "unknown"),
        bbox=bbox,
        text=str(block.get("block_content") or ""),
        order=_as_int(block.get("block_order")),
        block_id=_as_int(block.get("block_id")),
        group_id=_as_int(block.get("group_id")),
        image_path=image_path,
        image_url=images.get(image_path) if image_path else None,
    )


def _match_region_image(bbox: list[int], images: dict[str, str]) -> str | None:
    # The API embeds the source bbox in the cropped image filename
    # (e.g. ``imgs/img_in_image_box_260_253_960_685.jpg``), so an exact
    # coordinate match links an image block to its cropped file.
    if len(bbox) != 4:
        return None
    suffix = "_".join(str(value) for value in bbox)
    for path in images:
        if path.replace(".", "_").endswith(suffix) or suffix in path:
            return path
    return None


def _encode_multipart(
    boundary: str,
    fields: dict[str, str],
    filename: str,
    content: bytes,
) -> bytes:
    crlf = b"\r\n"
    parts: list[bytes] = []
    for name, value in fields.items():
        parts.append(f"--{boundary}".encode())
        parts.append(f'Content-Disposition: form-data; name="{name}"'.encode())
        parts.append(b"")
        parts.append(value.encode("utf-8"))
    parts.append(f"--{boundary}".encode())
    parts.append(
        f'Content-Disposition: form-data; name="file"; filename="{filename}"'.encode()
    )
    parts.append(b"Content-Type: application/octet-stream")
    parts.append(b"")
    body = crlf.join(parts) + crlf + content + crlf
    body += f"--{boundary}--".encode() + crlf
    return body


class _UrllibTransport:
    def request(
        self,
        method: str,
        url: str,
        *,
        headers: dict[str, str],
        body: bytes | None,
        timeout: float,
    ) -> HttpResponse:
        request = urllib.request.Request(url, data=body, headers=headers, method=method)
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.status, response.read()
        except urllib.error.HTTPError as exc:
            # Return the status so the client maps it to a readable OcrApiError;
            # the body may echo the request and is not surfaced (avoids leaking
            # the token or document content).
            return exc.code, b""
        except urllib.error.URLError as exc:
            raise OcrApiError(f"OCR request failed: {exc.reason}") from exc


def _load_json(raw: bytes | str) -> dict:
    try:
        text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
        parsed = json.loads(text)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OcrApiError(f"OCR response was not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise OcrApiError("OCR response was not a JSON object")
    return parsed


def _dig(payload: dict, *keys: str):
    current: object = payload
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _as_int(value: object) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def build_paddleocr_client(
    settings: Settings, *, transport: OcrTransport | None = None
) -> PaddleOcrApiClient:
    return PaddleOcrApiClient(settings, transport=transport)
