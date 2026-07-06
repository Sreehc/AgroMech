import json
import urllib.error

import pytest

from agromech_api.rag.generation.answer import (
    AnswerGenerationError,
    BailianAnswerGenerator,
    build_answer_generator,
)
from agromech_api.core.config import Settings


def bailian_settings(**overrides) -> Settings:
    base = {
        "_env_file": None,
        "file_storage_backend": "local",
        "graph_backend": "local",
        "model_provider": "bailian",
        "embedding_provider": "local",
        "bailian_api_key": "key",
        "bailian_base_url": "https://bailian.example/compatible-mode/v1",
        "llm_model": "qwen3.7-plus",
        "llm_request_timeout_seconds": 18.0,
        "local_file_storage_path": "./.agromech-data/storage/files",
    }
    base.update(overrides)
    return Settings(**base)


def sample_citations() -> list[dict[str, object]]:
    return [
        {
            "document_id": "doc-1",
            "document_title": "M7040 Manual",
            "chunk_id": "chunk-1",
            "source_locator": {"type": "text", "line_start": 1, "line_end": 2},
            "evidence_snippet": "Check hydraulic oil level before inspecting the pump.",
            "evidence_type": "text",
            "accessible": True,
        }
    ]


def test_bailian_answer_generator_sends_question_evidence_and_parses_sections() -> None:
    captured = {}

    def transport(request, timeout):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return {
            "choices": [
                {
                    "message": {
                        "content": json.dumps(
                            {
                                "answer": "建议先检查液压油油位，再检查液压泵。",
                                "sections": {
                                    "conclusion": "先检查液压油油位和液压泵。",
                                    "applicability": "适用于 M7040，以引用资料为准。",
                                    "possible_causes": ["液压油不足"],
                                    "inspection_steps": ["检查液压油油位"],
                                    "safety_reminder": ["停机并释放压力后再操作。"],
                                    "uncertainty": {"level": "low", "reasons": []},
                                },
                            }
                        )
                    }
                }
            ]
        }

    generator = BailianAnswerGenerator(bailian_settings(), transport=transport)

    result = generator.generate(
        question="M7040 E01 液压泵怎么排查？",
        citations=sample_citations(),
        safety_warnings=["停机并释放压力后再操作。"],
        uncertainty={"level": "low", "reasons": []},
        filters={"model": "M7040"},
    )

    assert result["answer"] == "建议先检查液压油油位，再检查液压泵。"
    assert result["sections"]["conclusion"] == "先检查液压油油位和液压泵。"
    assert result["sections"]["inspection_steps"] == ["检查液压油油位"]
    assert captured["url"] == "https://bailian.example/compatible-mode/v1/chat/completions"
    assert captured["timeout"] == 18.0
    assert captured["headers"]["Authorization"] == "Bearer key"
    assert captured["body"]["model"] == "qwen3.7-plus"
    user_prompt = captured["body"]["messages"][1]["content"]
    assert "M7040 E01 液压泵怎么排查？" in user_prompt
    assert "Check hydraulic oil level before inspecting the pump." in user_prompt


def test_bailian_answer_generator_rejects_malformed_response() -> None:
    generator = BailianAnswerGenerator(
        bailian_settings(),
        transport=lambda _request, _timeout: {"choices": [{"message": {"content": "{\"answer\": \"x\"}"}}]},
    )

    with pytest.raises(AnswerGenerationError, match="missing sections"):
        generator.generate(
            question="question",
            citations=sample_citations(),
            safety_warnings=[],
            uncertainty={"level": "low", "reasons": []},
            filters={},
        )


def test_bailian_answer_generator_wraps_transport_errors_without_leaking_citations() -> None:
    def transport(_request, _timeout):
        raise urllib.error.URLError("timeout while sending secret citation")

    generator = BailianAnswerGenerator(bailian_settings(), transport=transport)

    with pytest.raises(AnswerGenerationError) as exc_info:
        generator.generate(
            question="question",
            citations=sample_citations(),
            safety_warnings=[],
            uncertainty={"level": "low", "reasons": []},
            filters={},
        )

    message = str(exc_info.value)
    assert "secret citation" not in message
    assert "key" not in message


def test_build_answer_generator_selects_bailian_or_none() -> None:
    assert isinstance(build_answer_generator(bailian_settings()), BailianAnswerGenerator)
    assert build_answer_generator(
        Settings(
            _env_file=None,
            file_storage_backend="local",
            graph_backend="local",
            model_provider="local",
            embedding_provider="local",
        )
    ) is None
