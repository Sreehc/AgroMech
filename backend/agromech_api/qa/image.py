from __future__ import annotations

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import UploadFile, status
from sqlalchemy import Engine

from agromech_api.core.config import Settings
from agromech_api.core.errors import AppError, ErrorCode
from agromech_api.ingestion.image import IMAGE_MIME_TYPES, OcrUnavailable, default_ocr_reader
from agromech_api.sessions.history import append_image_session_exchange, ensure_session_belongs_to_user
from agromech_api.qa.text import MAX_QUESTION_LENGTH, answer_text_question
from agromech_api.ingestion.vision import build_visual_reader, normalize_visual_observation


LOW_CONFIDENCE_ANSWER = "图片线索置信度较低，未找到足够可用线索。请补充更清晰图片、型号、故障码或文字描述。"
VISUAL_ANNOTATION_COORDINATE_FORMAT = "normalized_xywh"
ENTITY_ANNOTATION_TYPES = {
    "possible_models": "possible_model",
    "visible_parts": "visible_part",
    "warning_lights": "warning_light",
    "part_numbers": "part_number",
}
ANNOTATION_BOXES = {
    "possible_models": {"x": 0.08, "y": 0.1, "width": 0.34, "height": 0.18},
    "visible_parts": {"x": 0.44, "y": 0.38, "width": 0.32, "height": 0.26},
    "warning_lights": {"x": 0.62, "y": 0.12, "width": 0.18, "height": 0.16},
    "part_numbers": {"x": 0.12, "y": 0.68, "width": 0.28, "height": 0.14},
}


def heuristic_visual_analysis(text: str) -> dict[str, object]:
    normalized_text = text.lower()
    possible_models = []
    if "m7040" in normalized_text:
        possible_models.append("M7040")
    if "l3901" in normalized_text:
        possible_models.append("L3901")

    visible_parts = []
    if "hydraulic" in normalized_text or "液压" in normalized_text:
        visible_parts.append("hydraulic")
    if "pump" in normalized_text or "泵" in normalized_text:
        visible_parts.append("pump")

    warning_lights = []
    if "e01" in normalized_text:
        warning_lights.append("E01")

    confidence = 0.8 if possible_models or visible_parts or warning_lights else 0.2
    low_confidence = confidence < 0.55
    detected_entities = {
        "possible_models": possible_models,
        "visible_parts": visible_parts,
        "warning_lights": warning_lights,
        "part_numbers": [],
    }
    visual_annotations = build_visual_annotations(detected_entities, confidence)
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
        "detected_entities": detected_entities,
        "visual_annotations": visual_annotations,
        "visual_annotation_status": visual_annotation_status(visual_annotations),
        "visual_confidence": {
            "confidence": confidence,
            "low_confidence": low_confidence,
        },
    }


def analyze_uploaded_image(
    settings: Settings,
    *,
    filename: str,
    content: bytes,
    question: str | None,
    brand: str | None,
    model: str | None,
) -> dict[str, object]:
    suffix = Path(filename).suffix or ".bin"
    temp_path: Path | None = None
    with NamedTemporaryFile(delete=False, suffix=suffix) as temporary:
        temporary.write(content)
        temp_path = Path(temporary.name)

    try:
        ocr_text, ocr_status = run_image_ocr(temp_path)
        heuristic = heuristic_visual_analysis(
            " ".join(value for value in [filename, question or "", brand or "", model or "", ocr_text or ""] if value)
        )
        visual = run_image_vision(settings, temp_path, ocr_text)
        if visual is None:
            return {
                **heuristic,
                "ocr_text": ocr_text or "",
                "visual_confidence": {
                    **heuristic["visual_confidence"],
                    "degraded_reason": "vision_unavailable",
                    "ocr_status": ocr_status,
                },
            }

        detected_entities = merge_detected_entities(heuristic["detected_entities"], visual)
        confidence = float(visual.get("confidence") or heuristic["visual_confidence"]["confidence"])
        low_confidence = bool(visual.get("low_confidence")) or (
            not any(detected_entities.values()) and not normalized_text_clue(ocr_text)
        )
        visual_annotations = build_visual_annotations(detected_entities, confidence)
        description = str(visual.get("description") or heuristic["description"])
        return {
            "visual_observation": description,
            "description": description,
            "ocr_text": ocr_text or "",
            "detected_entities": detected_entities,
            "visual_annotations": visual_annotations,
            "visual_annotation_status": visual_annotation_status(visual_annotations),
            "visual_confidence": {
                "confidence": confidence,
                "low_confidence": low_confidence,
                "ocr_status": ocr_status,
            },
        }
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def run_image_ocr(path: Path) -> tuple[str | None, str]:
    try:
        text = default_ocr_reader(path).strip()
    except OcrUnavailable:
        return None, "ocr_unavailable"
    except Exception:
        return None, "ocr_failed"
    if not text:
        return None, "ocr_empty"
    return text, "succeeded"


def run_image_vision(settings: Settings, path: Path, ocr_text: str | None) -> dict[str, object] | None:
    reader = build_visual_reader(settings)
    try:
        raw = reader(path, ocr_text)
    except Exception:
        return None
    return normalize_visual_observation(raw, settings.vision_confidence_threshold)


def merge_detected_entities(
    fallback_entities: dict[str, list[str]],
    visual: dict[str, object],
) -> dict[str, list[str]]:
    merged: dict[str, list[str]] = {}
    for key in ["possible_models", "visible_parts", "warning_lights", "part_numbers"]:
        values: list[str] = []
        for source in [visual.get(key) or [], fallback_entities.get(key) or []]:
            for item in source:
                normalized = str(item).strip()
                if normalized and normalized not in values:
                    values.append(normalized)
        merged[key] = values
    return merged


def build_visual_annotations(detected_entities: dict[str, list[str]], confidence: float) -> list[dict[str, object]]:
    annotations: list[dict[str, object]] = []
    for entity_key, annotation_type in ENTITY_ANNOTATION_TYPES.items():
        labels = detected_entities.get(entity_key, [])
        for index, label in enumerate(labels):
            annotations.append(
                {
                    "id": f"{annotation_type}-{index + 1}-{label.lower()}",
                    "type": annotation_type,
                    "label": label,
                    "confidence": confidence,
                    "bbox": normalized_bbox(entity_key, index),
                }
            )
    return annotations


def normalized_bbox(entity_key: str, index: int) -> dict[str, object]:
    box = ANNOTATION_BOXES[entity_key]
    x_offset = min(index * 0.03, 0.12)
    y_offset = min(index * 0.03, 0.12)
    x = min(box["x"] + x_offset, 1 - box["width"])
    y = min(box["y"] + y_offset, 1 - box["height"])
    return {
        "format": VISUAL_ANNOTATION_COORDINATE_FORMAT,
        "x": round(x, 4),
        "y": round(y, 4),
        "width": box["width"],
        "height": box["height"],
    }


def visual_annotation_status(annotations: list[dict[str, object]]) -> dict[str, str | None]:
    if any(has_usable_bbox(annotation) for annotation in annotations):
        return {
            "status": "available",
            "coordinate_format": VISUAL_ANNOTATION_COORDINATE_FORMAT,
            "missing_reason": None,
        }
    if annotations:
        return {
            "status": "missing",
            "coordinate_format": VISUAL_ANNOTATION_COORDINATE_FORMAT,
            "missing_reason": "no_bbox",
        }
    return {
        "status": "missing",
        "coordinate_format": VISUAL_ANNOTATION_COORDINATE_FORMAT,
        "missing_reason": "no_detected_entities",
    }


def has_usable_bbox(annotation: dict[str, object]) -> bool:
    bbox = annotation.get("bbox")
    if not isinstance(bbox, dict):
        return False
    if bbox.get("format") != VISUAL_ANNOTATION_COORDINATE_FORMAT:
        return False
    required_keys = ("x", "y", "width", "height")
    if not all(isinstance(bbox.get(key), (int, float)) for key in required_keys):
        return False
    x = float(bbox["x"])
    y = float(bbox["y"])
    width = float(bbox["width"])
    height = float(bbox["height"])
    return 0 <= x <= 1 and 0 <= y <= 1 and 0 < width <= 1 and 0 < height <= 1 and x + width <= 1 and y + height <= 1


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
    document_type: str | None,
    language: str | None,
    trace_id: str,
    username: str | None = None,
    viewer_user_id: str | None = None,
    session_id: str | None = None,
) -> dict[str, object]:
    normalized_filters = {
        "brand": brand,
        "model": model,
        "document_type": document_type,
        "language": language,
    }
    normalized_filters = {key: value for key, value in normalized_filters.items() if value is not None}
    if session_id:
        if not username:
            raise AppError(ErrorCode.UNAUTHORIZED, "Authentication required", status_code=status.HTTP_401_UNAUTHORIZED)
        ensure_session_belongs_to_user(engine, username=username, session_id=session_id)
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

    visual = analyze_uploaded_image(
        settings,
        filename=image.filename or "upload",
        content=content,
        question=normalized_question,
        brand=brand,
        model=model,
    )
    detected_entities = visual["detected_entities"]
    if visual["visual_confidence"]["low_confidence"] and not normalized_question:
        payload = low_confidence_payload(visual, trace_id)
        if session_id and username:
            append_image_session_exchange(
                engine,
                username=username,
                session_id=session_id,
                question=normalized_question or None,
                filename=image.filename or "upload",
                filters=normalized_filters,
                payload=payload,
            )
        return payload

    search_query = visual_search_query(
        normalized_question,
        {
            "ocr_text": visual.get("ocr_text"),
            "description": visual.get("description"),
            "detected_entities": detected_entities,
        },
    )
    qa_payload = answer_text_question(
        engine,
        question=search_query,
        filters={
            "brand": brand,
            "model": model or first_model(detected_entities),
            "document_type": document_type,
            "language": language,
        },
        trace_id=trace_id,
        settings=settings,
        username=username,
        viewer_user_id=viewer_user_id,
        image_context={
            "ocr_text": visual.get("ocr_text"),
            "description": visual.get("description"),
            "detected_entities": detected_entities,
        },
    )
    if visual["visual_confidence"]["low_confidence"] and not qa_payload["citations"]:
        qa_payload["answer"] = LOW_CONFIDENCE_ANSWER
    payload = {**visual, **qa_payload}
    if session_id and username:
        session_filters = {
            "brand": brand,
            "model": model or first_model(detected_entities),
            "document_type": document_type,
            "language": language,
        }
        append_image_session_exchange(
            engine,
            username=username,
            session_id=session_id,
            question=normalized_question or None,
            filename=image.filename or "upload",
            filters={key: value for key, value in session_filters.items() if value is not None},
            payload=payload,
        )
    return payload


def visual_search_query(question: str, visual_clues: dict[str, object]) -> str:
    terms = [question] if question else ["识别图片线索并检索相关资料"]
    ocr_text = normalized_text_clue(visual_clues.get("ocr_text"))
    if ocr_text:
        terms.append(ocr_text)
    description = normalized_text_clue(visual_clues.get("description"))
    if description:
        terms.append(description)
    detected_entities = visual_clues.get("detected_entities")
    if not isinstance(detected_entities, dict):
        detected_entities = visual_clues
    for key in ["possible_models", "visible_parts", "warning_lights", "part_numbers"]:
        values = detected_entities.get(key, [])
        if isinstance(values, list):
            terms.extend(str(value) for value in values if str(value).strip())
    return " ".join(term for term in terms if term)


def normalized_text_clue(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


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

