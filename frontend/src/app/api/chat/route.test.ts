import { describe, expect, it } from "vitest";

import { POST } from "./route";

describe("/api/chat route contract", () => {
  it("forwards filters and session id to the text QA backend", async () => {
    const originalFetch = globalThis.fetch;
    const backendRequests: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];

    globalThis.fetch = (async (input, init) => {
      backendRequests.push({ input, init });

      if (String(input).endsWith("/auth/login")) {
        return new Response(JSON.stringify({ access_token: "backend-token" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (String(input).endsWith("/qa/text")) {
        return new Response(
          JSON.stringify({
            answer: "根据资料检查液压泵压力。",
            sections: {},
            citations: [
              {
                document_id: "doc-route",
                document_title: "M7040 维修手册",
                chunk_id: "chunk-route",
                source_locator: { page: 18 },
                evidence_snippet: "E01 需要检查液压泵压力。",
                evidence_type: "text",
                accessible: true,
              },
            ],
            trace_id: "trace-route",
            uncertainty: { level: "low", reasons: [] },
            safety_warnings: ["维修液压系统前先释放高压。"],
            visual_observation: "图片显示液压告警灯。",
            ocr_text: "E01",
            detected_entities: [{ label: "hydraulic_warning", confidence: 0.82 }],
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      return new Response("unexpected request", { status: 500 });
    }) as typeof fetch;

    try {
      const response = await POST(
        new Request("http://localhost/api/chat", {
          method: "POST",
          body: JSON.stringify({
            messages: [
              {
                id: "message-1",
                role: "user",
                parts: [{ type: "text", text: "E01 液压告警怎么排查？" }],
              },
            ],
            filters: {
              brand: " Kubota ",
              model: "M7040",
              document_type: "manual",
              language: " zh-CN ",
            },
            session_id: "session-route",
          }),
        }),
      );

      const responseBody = await response.text();

      const qaRequest = backendRequests.find((request) => String(request.input).endsWith("/qa/text"));
      expect(qaRequest).toBeDefined();
      expect(qaRequest?.init?.headers).toEqual(
        expect.objectContaining({
          Authorization: "Bearer backend-token",
          "Content-Type": "application/json",
        }),
      );
      expect(JSON.parse(String(qaRequest?.init?.body))).toEqual({
        question: "E01 液压告警怎么排查？",
        filters: {
          brand: "Kubota",
          model: "M7040",
          document_type: "manual",
          language: "zh-CN",
        },
        session_id: "session-route",
      });
      expect(responseBody).toContain("data-agromech-payload");
      expect(responseBody).toContain("根据资料检查液压泵压力。");
      expect(responseBody).toContain("M7040 维修手册");
      expect(responseBody).toContain("trace-route");
      expect(responseBody).toContain("维修液压系统前先释放高压。");
      expect(responseBody).toContain("图片显示液压告警灯。");
      expect(responseBody).toContain("E01");
      expect(responseBody).toContain("hydraulic_warning");
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("forwards filters and session id to the image QA backend", async () => {
    const originalFetch = globalThis.fetch;
    const backendRequests: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];

    globalThis.fetch = (async (input, init) => {
      backendRequests.push({ input, init });

      if (String(input).startsWith("data:image/png")) {
        return new Response(new Blob(["fake-image"], { type: "image/png" }));
      }

      if (String(input).endsWith("/auth/login")) {
        return new Response(JSON.stringify({ access_token: "backend-token" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      if (String(input).endsWith("/qa/image")) {
        return new Response(
          JSON.stringify({
            answer: "图片显示 E01 告警，建议检查液压系统。",
            sections: {},
            citations: [],
            trace_id: "trace-image-route",
            uncertainty: { level: "low", reasons: [] },
            safety_warnings: [],
            visual_observation: "possible model M7040; warning E01",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }

      return new Response("unexpected request", { status: 500 });
    }) as typeof fetch;

    try {
      const response = await POST(
        new Request("http://localhost/api/chat", {
          method: "POST",
          body: JSON.stringify({
            messages: [
              {
                id: "message-1",
                role: "user",
                parts: [
                  { type: "text", text: "这张图的 E01 告警怎么排查？" },
                  {
                    type: "file",
                    mediaType: "image/png",
                    filename: "dashboard.png",
                    url: "data:image/png;base64,aGVsbG8=",
                  },
                ],
              },
            ],
            filters: {
              brand: " Kubota ",
              model: " M7040 ",
              document_type: "manual",
              language: "zh-CN",
            },
            session_id: "session-image-route",
          }),
        }),
      );

      await response.text();

      const qaRequest = backendRequests.find((request) => String(request.input).endsWith("/qa/image"));
      expect(qaRequest).toBeDefined();
      expect(qaRequest?.init?.headers).toEqual(
        expect.objectContaining({
          Authorization: "Bearer backend-token",
        }),
      );
      const formData = qaRequest?.init?.body as FormData;
      expect(formData.get("question")).toBe("这张图的 E01 告警怎么排查？");
      expect(formData.get("brand")).toBe("Kubota");
      expect(formData.get("model")).toBe("M7040");
      expect(formData.get("document_type")).toBe("manual");
      expect(formData.get("language")).toBe("zh-CN");
      expect(formData.get("session_id")).toBe("session-image-route");
      expect(formData.get("image")).toBeInstanceOf(Blob);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
