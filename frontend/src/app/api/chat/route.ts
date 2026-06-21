import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessage,
} from "ai";

import {
  createAgroMechPayloadDataPart,
  extractAgroMechRequest,
  formatAgroMechAnswer,
  type AgroMechChatContext,
  type AgroMechContextFilters,
  type AgroMechQaResponse,
} from "@/lib/agromech-chat";

const API_BASE_URL = process.env.AGROMECH_API_BASE_URL ?? "http://127.0.0.1:8000";
const ADMIN_USERNAME = process.env.AGROMECH_ADMIN_USERNAME ?? "admin";
const ADMIN_PASSWORD = process.env.AGROMECH_ADMIN_PASSWORD ?? "change-me";

async function getAccessToken(): Promise<string> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: ADMIN_USERNAME, password: ADMIN_PASSWORD }),
  });

  if (!response.ok) {
    throw new Error("AgroMech authentication failed.");
  }

  const payload = (await response.json()) as { access_token: string };
  return payload.access_token;
}

async function dataUrlToBlob(dataUrl: string): Promise<Blob> {
  const response = await fetch(dataUrl);
  return response.blob();
}

type AgroMechRouteRequest = {
  messages: UIMessage[];
  filters?: AgroMechContextFilters;
  session_id?: string | null;
};

async function askAgroMech(messages: UIMessage[], context: AgroMechChatContext): Promise<AgroMechQaResponse> {
  const request = extractAgroMechRequest(messages, context);
  const token = await getAccessToken();

  if (request.image) {
    const formData = new FormData();
    formData.append(
      "image",
      await dataUrlToBlob(request.image.dataUrl),
      request.image.filename,
    );
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

    const response = await fetch(`${API_BASE_URL}/qa/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
    });

    if (!response.ok) {
      throw new Error("AgroMech image question failed.");
    }

    return {
      ...((await response.json()) as AgroMechQaResponse),
      question_image: request.image,
    };
  }

  const response = await fetch(`${API_BASE_URL}/qa/text`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      question: request.question,
      filters: request.filters,
      session_id: request.session_id,
    }),
  });

  if (!response.ok) {
    throw new Error("AgroMech text question failed.");
  }

  return (await response.json()) as AgroMechQaResponse;
}

export async function POST(req: Request) {
  const { messages, filters, session_id }: AgroMechRouteRequest = await req.json();

  return createUIMessageStreamResponse({
    stream: createUIMessageStream({
      originalMessages: messages,
      execute: async ({ writer }) => {
        const payload = await askAgroMech(messages, { filters, session_id });
        const answer = formatAgroMechAnswer(payload);
        const textId = "agromech-answer";

        writer.write({ type: "start" });
        writer.write(createAgroMechPayloadDataPart(payload));
        writer.write({ type: "text-start", id: textId });
        writer.write({ type: "text-delta", id: textId, delta: answer });
        writer.write({ type: "text-end", id: textId });
        writer.write({ type: "finish", finishReason: "stop" });
      },
      onError: () => "AgroMech 助手暂时无法完成这次回答，请检查后端服务和认证配置。",
    }),
  });
}
