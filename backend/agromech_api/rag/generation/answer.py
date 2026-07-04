from __future__ import annotations

import json
import urllib.error
import urllib.request
from collections.abc import Callable
from typing import Protocol

from agromech_api.core.config import Settings


AnswerTransport = Callable[[urllib.request.Request, float], dict[str, object]]


class AnswerGenerator(Protocol):
    provider: str
    model: str

    def generate(
        self,
        *,
        question: str,
        citations: list[dict[str, object]],
        safety_warnings: list[str],
        uncertainty: dict[str, object],
        filters: dict[str, object],
    ) -> dict[str, object]: ...


class AnswerGenerationError(RuntimeError):
    """Raised when the configured answer model cannot return a usable answer."""


class BailianAnswerGenerator:
    provider = "bailian"

    def __init__(
        self,
        settings: Settings,
        *,
        model_override: str | None = None,
        transport: AnswerTransport | None = None,
    ) -> None:
        self.model = model_override or settings.llm_model
        self.timeout = settings.llm_request_timeout_seconds
        self._api_key = settings.bailian_api_key
        self._base_url = settings.bailian_base_url.rstrip("/")
        self._transport = transport or self._default_transport

    def generate(
        self,
        *,
        question: str,
        citations: list[dict[str, object]],
        safety_warnings: list[str],
        uncertainty: dict[str, object],
        filters: dict[str, object],
    ) -> dict[str, object]:
        request = self._request(
            question=question,
            citations=citations,
            safety_warnings=safety_warnings,
            uncertainty=uncertainty,
            filters=filters,
        )
        try:
            body = self._transport(request, self.timeout)
        except AnswerGenerationError:
            raise
        except urllib.error.HTTPError as exc:
            raise AnswerGenerationError(f"LLM request failed with HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise AnswerGenerationError("LLM request failed: upstream unavailable") from exc
        except Exception as exc:  # noqa: BLE001 - normalize for API error handling
            raise AnswerGenerationError(f"LLM request failed: {exc}") from exc
        return parse_bailian_answer_response(body)

    def _request(
        self,
        *,
        question: str,
        citations: list[dict[str, object]],
        safety_warnings: list[str],
        uncertainty: dict[str, object],
        filters: dict[str, object],
    ) -> urllib.request.Request:
        if not self._base_url:
            raise AnswerGenerationError("model_provider=bailian requires BAILIAN_BASE_URL to be configured")

        evidence_lines = []
        for index, citation in enumerate(citations, start=1):
            evidence_lines.append(
                f"[{index}] {citation['document_title']} / {citation['chunk_id']} / "
                f"{citation['source_locator']} / {citation['evidence_snippet']}"
            )
        payload = json.dumps(
            {
                "model": self.model,
                "messages": [
                    {
                        "role": "system",
                        "content": (
                            "You answer agricultural machinery maintenance questions only from provided evidence. "
                            "Return strict JSON with answer and sections. "
                            "Sections must include conclusion, applicability, possible_causes, inspection_steps, "
                            "safety_reminder, citations, and uncertainty. "
                            "If evidence is insufficient, say so plainly and do not invent facts."
                        ),
                    },
                    {
                        "role": "user",
                        "content": (
                            f"Question: {question}\n"
                            f"Filters: {json.dumps(filters, ensure_ascii=False)}\n"
                            f"Uncertainty: {json.dumps(uncertainty, ensure_ascii=False)}\n"
                            f"Safety warnings: {json.dumps(safety_warnings, ensure_ascii=False)}\n"
                            "Evidence:\n"
                            + "\n".join(evidence_lines)
                        ),
                    },
                ],
                "response_format": {"type": "json_object"},
            }
        ).encode("utf-8")
        return urllib.request.Request(
            f"{self._base_url}/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

    def _default_transport(
        self,
        request: urllib.request.Request,
        timeout: float,
    ) -> dict[str, object]:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))


def parse_bailian_answer_response(body: dict[str, object]) -> dict[str, object]:
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise AnswerGenerationError("LLM response missing choices")
    first_choice = choices[0]
    if not isinstance(first_choice, dict):
        raise AnswerGenerationError("LLM response choice is invalid")
    message = first_choice.get("message")
    if not isinstance(message, dict):
        raise AnswerGenerationError("LLM response missing message")
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(str(item.get("text", "")) for item in content if isinstance(item, dict))
    if not isinstance(content, str) or not content.strip():
        raise AnswerGenerationError("LLM response missing content")
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError as exc:
        raise AnswerGenerationError("LLM response content is not valid JSON") from exc
    if not isinstance(parsed, dict):
        raise AnswerGenerationError("LLM response JSON must be an object")
    if "answer" not in parsed:
        raise AnswerGenerationError("LLM response missing answer")
    if "sections" not in parsed:
        raise AnswerGenerationError("LLM response missing sections")
    return parsed


def build_answer_generator(
    settings: Settings,
    *,
    model_override: str | None = None,
    transport: AnswerTransport | None = None,
) -> AnswerGenerator | None:
    if settings.model_provider == "bailian":
        return BailianAnswerGenerator(settings, model_override=model_override, transport=transport)
    return None
