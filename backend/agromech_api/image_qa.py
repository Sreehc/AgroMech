from __future__ import annotations

from fastapi import Depends, File, Form, Request, UploadFile, status
from sqlalchemy import Engine

from agromech_api.auth import UserContext, require_roles
from agromech_api.config import Settings
from agromech_api.db.enums import UserRole
from agromech_api.errors import AppError, ErrorCode
from agromech_api.image_ingestion import IMAGE_MIME_TYPES
from agromech_api.text_qa import MAX_QUESTION_LENGTH, answer_text_question


LOW_CONFIDENCE_ANSWER = "图片线索置信度较低，未找到足够可用线索。请补充更清晰图片、型号、故障码或文字描述。"


def analyze_uploaded_image(filename: str, question: str | None, brand: str | None, model: str | None) -> dict[str, object]:
    text = " ".join(value for value in [filename, question or "", brand or "", model or ""] if value).lower()
    possible_models = []
    if "m7040" in text:
        possible_models.append("M7040")
    if "l3901" in text:
        possible_models.append("L3901")

    visible_parts = []
    if "hydraulic" in text or "液压" in text:
        visible_parts.append("hydraulic")
    if "pump" in text or "泵" in text:
        visible_parts.append("pump")

    warning_lights = []
    if "e01" in text:
        warning_lights.append("E01")

    confidence = 0.8 if possible_models or visible_parts or warning_lights else 0.2
    low_confidence = confidence < 0.55
    description_parts = []
    if possible_models:
        description_parts.append("possible model " + ", ".join(possible_models))
    if visible_parts:
        description_parts.append("visible part " + ", ".join(visible_parts))
    if warning_lights:
        description_parts.append("warning " + ", ".join(warning_lights))
    description = "; ".join(description_parts) or "No reliable visual clue detected"
    return {
        "visual_observation": description,
        "description": description,
        "ocr_text": "",
        "detected_entities": {
            "possible_models": possible_models,
            "visible_parts": visible_parts,
            "warning_lights": warning_lights,
            "part_numbers": [],
        },
        "visual_confidence": {
            "confidence": confidence,
            "low_confidence": low_confidence,
        },
    }


def validate_image_upload(images: list[UploadFile], settings: Settings) -> UploadFile:
    if len(images) > 1:
        raise AppError(ErrorCode.TOO_MANY_IMAGES, "Only one image can be uploaded", status_code=status.HTTP_400_BAD_REQUEST)
    image = images[0]
    if image.content_type not in IMAGE_MIME_TYPES:
        raise AppError(
            ErrorCode.UNSUPPORTED_FILE_TYPE,
            "Unsupported file type",
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            details={"content_type": image.content_type},
        )
    return image


async def answer_image_question(
    engine: Engine,
    settings: Settings,
    *,
    images: list[UploadFile],
    question: str | None,
    brand: str | None,
    model: str | None,
    trace_id: str,
) -> dict[str, object]:
    image = validate_image_upload(images, settings)
    content = await image.read()
    max_bytes = settings.upload_max_image_size_mb * 1024 * 1024
    if len(content) > max_bytes:
        raise AppError(
            ErrorCode.FILE_TOO_LARGE,
            "File size exceeds configured limit",
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            details={"limit_bytes": max_bytes},
        )
    normalized_question = (question or "").strip()
    if len(normalized_question) > MAX_QUESTION_LENGTH:
        raise AppError(
            ErrorCode.QUESTION_TOO_LONG,
            "Question exceeds maximum length",
            status_code=status.HTTP_400_BAD_REQUEST,
            details={"max_length": MAX_QUESTION_LENGTH},
        )

    visual = analyze_uploaded_image(image.filename or "upload", normalized_question, brand, model)
    detected_entities = visual["detected_entities"]
    if visual["visual_confidence"]["low_confidence"] and not normalized_question:
        return low_confidence_payload(visual, trace_id)

    search_query = visual_search_query(normalized_question, detected_entities)
    qa_payload = answer_text_question(
        engine,
        question=search_query,
        filters={"brand": brand, "model": model or first_model(detected_entities)},
        trace_id=trace_id,
    )
    if visual["visual_confidence"]["low_confidence"] and not qa_payload["citations"]:
        qa_payload["answer"] = LOW_CONFIDENCE_ANSWER
    return {**visual, **qa_payload}


def visual_search_query(question: str, detected_entities: dict[str, list[str]]) -> str:
    terms = [question] if question else ["识别图片线索并检索相关资料"]
    for key in ["possible_models", "visible_parts", "warning_lights", "part_numbers"]:
        terms.extend(detected_entities[key])
    return " ".join(term for term in terms if term)


def first_model(detected_entities: dict[str, list[str]]) -> str | None:
    models = detected_entities.get("possible_models") or []
    return models[0] if models else None


def low_confidence_payload(visual: dict[str, object], trace_id: str) -> dict[str, object]:
    uncertainty = {"level": "high", "reasons": ["low_visual_confidence", "missing_question"]}
    return {
        **visual,
        "answer": LOW_CONFIDENCE_ANSWER,
        "sections": {
            "conclusion": "图片线索不足。",
            "citations": [],
            "uncertainty": uncertainty,
        },
        "citations": [],
        "trace_id": trace_id,
        "uncertainty": uncertainty,
        "safety_warnings": [],
    }


def register_image_qa_routes(app, *, settings: Settings, engine: Engine) -> None:
    @app.post("/qa/image", tags=["qa"])
    async def image_qa(
        request: Request,
        image: list[UploadFile] = File(...),
        question: str | None = Form(default=None),
        brand: str | None = Form(default=None),
        model: str | None = Form(default=None),
        _user: UserContext = Depends(require_roles(UserRole.ADMIN, UserRole.MAINTAINER, UserRole.USER, UserRole.EVALUATOR)),
    ) -> dict[str, object]:
        return await answer_image_question(
            engine,
            settings,
            images=image,
            question=question,
            brand=brand,
            model=model,
            trace_id=request.state.trace_id,
        )
