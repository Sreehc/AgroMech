import type { UIMessage } from "ai";
import type { ChatTransport } from "ai";
import type { UIMessageChunk } from "ai";

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

export type AgroMechChatTransportContext = {
  token?: string;
  filters?: AgroMechContextFilters;
  sessionId?: string;
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

export function createAgroMechChatTransport(
  context: AgroMechChatTransportContext,
): ChatTransport<UIMessage> {
  return {
    async sendMessages({ messages, abortSignal }) {
      const payload = await askAgroMechBackend(messages, context, abortSignal);
      return qaPayloadStream(payload);
    },
    async reconnectToStream() {
      return null;
    },
  };
}

async function askAgroMechBackend(
  messages: UIMessage[],
  context: AgroMechChatTransportContext,
  abortSignal: AbortSignal | undefined,
): Promise<AgroMechQaResponse> {
  const request = extractAgroMechRequest(messages, {
    filters: context.filters,
    session_id: context.sessionId,
  });
  // 文本问答支持匿名（后端放开 /qa/text 并按 IP 限流）；图片问答仍需登录。
  const authorization = context.token
    ? { Authorization: `Bearer ${context.token}` }
    : undefined;

  if (request.image) {
    if (!context.token) {
      throw new Error("请先登录再上传图片提问。");
    }
    const formData = new FormData();
    formData.append("image", await dataUrlToBlob(request.image.dataUrl), request.image.filename);
    if (request.question) {
      formData.append("question", request.question);
    }
    Object.entries(request.filters).forEach(([key, value]) => {
      if (value) {
        formData.append(key, value);
      }
    });
    if (request.session_id) {
      formData.append("session_id", request.session_id);
    }
    const response = await fetch("/backend/qa/image", {
      method: "POST",
      headers: authorization,
      body: formData,
      signal: abortSignal,
    });
    if (!response.ok) {
      throw new Error("AgroMech image question failed.");
    }
    return {
      ...((await response.json()) as AgroMechQaResponse),
      question_image: request.image,
    };
  }

  const textHeaders: Record<string, string> = { "Content-Type": "application/json" };
  if (authorization) {
    Object.assign(textHeaders, authorization);
  }
  const response = await fetch("/backend/qa/text", {
    method: "POST",
    headers: textHeaders,
    body: JSON.stringify({
      question: request.question,
      filters: request.filters,
      session_id: request.session_id,
    }),
    signal: abortSignal,
  });
  if (!response.ok) {
    throw new Error("AgroMech text question failed.");
  }
  return (await response.json()) as AgroMechQaResponse;
}

async function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  const response = await fetch(dataUrl);
  return response.blob();
}

function qaPayloadStream(payload: AgroMechQaResponse): ReadableStream<UIMessageChunk> {
  const answer = formatAgroMechAnswer(payload);
  const textId = "agromech-answer";
  return new ReadableStream<UIMessageChunk>({
    start(controller) {
      controller.enqueue({ type: "start" });
      controller.enqueue(createAgroMechPayloadDataPart(payload));
      controller.enqueue({ type: "text-start", id: textId });
      controller.enqueue({ type: "text-delta", id: textId, delta: answer });
      controller.enqueue({ type: "text-end", id: textId });
      controller.enqueue({ type: "finish", finishReason: "stop" });
      controller.close();
    },
  });
}
