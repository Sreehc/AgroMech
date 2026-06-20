import {
  createUIMessageStream,
  createUIMessageStreamResponse,
  type UIMessage,
} from "ai";

import {
  extractAgroMechRequest,
  formatAgroMechAnswer,
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

async function askAgroMech(messages: UIMessage[]): Promise<AgroMechQaResponse> {
  const request = extractAgroMechRequest(messages);
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

    const response = await fetch(`${API_BASE_URL}/qa/image`, {
      method: "POST",
      headers: { Authorization: `Bearer ${token}` },
      body: formData,
    });

    if (!response.ok) {
      throw new Error("AgroMech image question failed.");
    }

    return (await response.json()) as AgroMechQaResponse;
  }

  const response = await fetch(`${API_BASE_URL}/qa/text`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${token}`,
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ question: request.question, filters: {} }),
  });

  if (!response.ok) {
    throw new Error("AgroMech text question failed.");
  }

  return (await response.json()) as AgroMechQaResponse;
}

export async function POST(req: Request) {
  const { messages }: { messages: UIMessage[] } = await req.json();

  return createUIMessageStreamResponse({
    stream: createUIMessageStream({
      originalMessages: messages,
      execute: async ({ writer }) => {
        const answer = formatAgroMechAnswer(await askAgroMech(messages));
        const textId = "agromech-answer";

        writer.write({ type: "start" });
        writer.write({ type: "text-start", id: textId });
        writer.write({ type: "text-delta", id: textId, delta: answer });
        writer.write({ type: "text-end", id: textId });
        writer.write({ type: "finish", finishReason: "stop" });
      },
      onError: () => "AgroMech 助手暂时无法完成这次回答，请检查后端服务和认证配置。",
    }),
  });
}
