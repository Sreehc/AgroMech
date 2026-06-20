import type { UIMessage } from "ai";

export type AgroMechImageAttachment = {
  dataUrl: string;
  filename: string;
  mediaType: string;
};

export type AgroMechChatRequest = {
  question: string;
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

export type AgroMechQaResponse = {
  answer: string;
  sections?: Record<string, unknown>;
  citations: AgroMechCitation[];
  trace_id: string;
  uncertainty: { level: string; reasons: string[] };
  safety_warnings: string[];
  visual_observation?: string;
  ocr_text?: string;
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

export function extractAgroMechRequest(messages: UIMessage[]): AgroMechChatRequest {
  const lastUserMessage = messages.findLast((message) => message.role === "user");

  if (!lastUserMessage) {
    throw new Error("No user message found.");
  }

  const question = textFromMessage(lastUserMessage);
  const image = imageFromMessage(lastUserMessage);

  if (!question && !image) {
    throw new Error("Please enter a question or attach an image.");
  }

  return image ? { question, image } : { question };
}

function formatLocator(locator: Record<string, unknown>): string {
  const entries = Object.entries(locator);
  if (entries.length === 0) {
    return "source locator unavailable";
  }
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(", ");
}

function formatCitations(citations: AgroMechCitation[]): string {
  if (citations.length === 0) {
    return "未返回可引用来源。";
  }

  return citations
    .map((citation, index) => {
      const access = citation.accessible ? "可访问" : "不可访问";
      return `${index + 1}. ${citation.document_title} (${access}, ${formatLocator(
        citation.source_locator,
      )})\n   ${citation.evidence_snippet}`;
    })
    .join("\n");
}

export function formatAgroMechAnswer(payload: AgroMechQaResponse): string {
  const blocks = [payload.answer.trim()];

  if (payload.visual_observation?.trim()) {
    blocks.push(`### 视觉观察\n${payload.visual_observation.trim()}`);
  }

  if (payload.ocr_text?.trim()) {
    blocks.push(`### OCR 文本\n${payload.ocr_text.trim()}`);
  }

  if (payload.safety_warnings.length > 0) {
    blocks.push(`### 安全提醒\n${payload.safety_warnings.map((warning) => `- ${warning}`).join("\n")}`);
  }

  blocks.push(`### 引用来源\n${formatCitations(payload.citations)}`);

  const uncertaintyReasons = payload.uncertainty.reasons.length
    ? `，原因：${payload.uncertainty.reasons.join("、")}`
    : "";
  blocks.push(`### 调试信息\n- Trace ID: ${payload.trace_id}\n- 不确定性: ${payload.uncertainty.level}${uncertaintyReasons}`);

  return blocks.join("\n\n");
}
