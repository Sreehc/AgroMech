import type { UIMessage } from "ai";

export type AgroMechImageAttachment = {
  dataUrl: string;
  filename: string;
  mediaType: string;
};

export type AgroMechContextFilters = Partial<Record<"brand" | "model" | "document_type" | "language", string>>;

export type AgroMechChatContext = {
  filters?: AgroMechContextFilters;
  session_id?: string | null;
};

export type AgroMechChatRequest = {
  question: string;
  filters: AgroMechContextFilters;
  session_id?: string;
  image?: AgroMechImageAttachment;
};

export type AgroMechCitation = {
  document_id: string | null;
  document_title: string;
  chunk_id: string | null;
  source_locator: Record<string, unknown>;
  evidence_snippet: string;
  evidence_type: string;
  accessible: boolean;
};

export type AgroMechVisualAnnotationBox = {
  format: "normalized_xywh";
  x: number;
  y: number;
  width: number;
  height: number;
};

export type AgroMechVisualAnnotation = {
  id: string;
  type: string;
  label: string;
  confidence?: number;
  bbox?: AgroMechVisualAnnotationBox;
};

export type AgroMechVisualAnnotationStatus = {
  status: "available" | "missing" | string;
  coordinate_format: "normalized_xywh" | string;
  missing_reason: string | null;
};

export type AgroMechQaResponse = {
  answer: string;
  sections?: Record<string, unknown>;
  citations?: AgroMechCitation[];
  trace_id?: string;
  uncertainty?: { level: string; reasons: string[] };
  safety_warnings?: string[];
  visual_observation?: string;
  ocr_text?: string;
  detected_entities?: unknown;
  visual_annotations?: AgroMechVisualAnnotation[];
  visual_annotation_status?: AgroMechVisualAnnotationStatus;
  visual_confidence?: unknown;
  question_image?: AgroMechImageAttachment;
};

export type AgroMechStructuredPayload = {
  answer: string;
  sections: Record<string, unknown>;
  citations: AgroMechCitation[];
  trace_id: string | null;
  uncertainty: { level: string; reasons: string[] };
  safety_warnings: string[];
  visual_observation?: string;
  ocr_text?: string;
  detected_entities?: unknown;
  visual_annotations?: AgroMechVisualAnnotation[];
  visual_annotation_status?: AgroMechVisualAnnotationStatus;
  visual_confidence?: unknown;
  question_image?: AgroMechImageAttachment;
};

export type AgroMechEvidenceSelection = {
  payload: AgroMechStructuredPayload;
  citationIndex: number;
};

export type AgroMechPayloadDataPart = {
  type: "data-agromech-payload";
  id: "agromech-payload";
  data: AgroMechStructuredPayload;
};

function textFromMessage(message: UIMessage): string {
  return message.parts
    .filter((part) => part.type === "text")
    .map((part) => part.text)
    .join("\n")
    .trim();
}

function imageFromMessage(message: UIMessage): AgroMechImageAttachment | undefined {
  const filePart = message.parts.find(
    (part) =>
      part.type === "file" &&
      part.mediaType.startsWith("image/") &&
      part.url.startsWith("data:"),
  );

  if (!filePart || filePart.type !== "file") {
    return undefined;
  }

  return {
    dataUrl: filePart.url,
    filename: filePart.filename || "question-image",
    mediaType: filePart.mediaType,
  };
}

export function cleanAgroMechFilters(filters: AgroMechContextFilters | undefined): AgroMechContextFilters {
  const cleaned: AgroMechContextFilters = {};
  (["brand", "model", "document_type", "language"] as const).forEach((key) => {
    const value = filters?.[key]?.trim();
    if (value) {
      cleaned[key] = value;
    }
  });
  return cleaned;
}

function cleanSessionId(sessionId: string | null | undefined): string | undefined {
  const value = sessionId?.trim();
  return value || undefined;
}

export function extractAgroMechRequest(
  messages: UIMessage[],
  context: AgroMechChatContext = {},
): AgroMechChatRequest {
  const lastUserMessage = messages.findLast((message) => message.role === "user");

  if (!lastUserMessage) {
    throw new Error("No user message found.");
  }

  const question = textFromMessage(lastUserMessage);
  const image = imageFromMessage(lastUserMessage);

  if (!question && !image) {
    throw new Error("Please enter a question or attach an image.");
  }

  const request: AgroMechChatRequest = {
    question,
    filters: cleanAgroMechFilters(context.filters),
  };
  const sessionId = cleanSessionId(context.session_id);
  if (sessionId) {
    request.session_id = sessionId;
  }
  if (image) {
    request.image = image;
  }
  return request;
}

export function normalizeAgroMechPayload(payload: AgroMechQaResponse): AgroMechStructuredPayload {
  const normalized: AgroMechStructuredPayload = {
    answer: payload.answer,
    sections: payload.sections ?? {},
    citations: payload.citations ?? [],
    trace_id: payload.trace_id ?? null,
    uncertainty: payload.uncertainty ?? { level: "unknown", reasons: [] },
    safety_warnings: payload.safety_warnings ?? [],
  };

  if (payload.visual_observation !== undefined) {
    normalized.visual_observation = payload.visual_observation;
  }
  if (payload.ocr_text !== undefined) {
    normalized.ocr_text = payload.ocr_text;
  }
  if (payload.detected_entities !== undefined) {
    normalized.detected_entities = payload.detected_entities;
  }
  if (payload.visual_annotations !== undefined) {
    normalized.visual_annotations = payload.visual_annotations;
  }
  if (payload.visual_annotation_status !== undefined) {
    normalized.visual_annotation_status = payload.visual_annotation_status;
  }
  if (payload.visual_confidence !== undefined) {
    normalized.visual_confidence = payload.visual_confidence;
  }
  if (payload.question_image !== undefined) {
    normalized.question_image = payload.question_image;
  }

  return normalized;
}

export function createAgroMechPayloadDataPart(payload: AgroMechQaResponse): AgroMechPayloadDataPart {
  return {
    type: "data-agromech-payload",
    id: "agromech-payload",
    data: normalizeAgroMechPayload(payload),
  };
}

export function formatAgroMechAnswer(payload: AgroMechQaResponse): string {
  const structuredPayload = normalizeAgroMechPayload(payload);
  return structuredPayload.answer.trim() || "未返回回答内容。";
}
