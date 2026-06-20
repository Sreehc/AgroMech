import { describe, expect, it } from "vitest";

import {
  canMutateLibrary,
  createChatSession,
  deleteChatSession,
  documentQueryString,
  documentStatusPresentation,
  documentStatuses,
  getChatSession,
  getDocument,
  getDocumentPreview,
  isKnownDocumentStatus,
  listChatSessions,
  updateChatSession,
} from "./frontend-api";

describe("frontend API helpers", () => {
  it("builds document query strings from non-empty filters", () => {
    expect(
      documentQueryString({
        brand: "Kubota",
        model: " ",
        document_type: "manual",
        language: "",
        status: "indexed",
      }),
    ).toBe("?brand=Kubota&document_type=manual&status=indexed");
  });

  it("only allows admin and maintainer roles to mutate the library", () => {
    expect(canMutateLibrary("admin")).toBe(true);
    expect(canMutateLibrary("maintainer")).toBe(true);
    expect(canMutateLibrary("user")).toBe(false);
    expect(canMutateLibrary("evaluator")).toBe(false);
  });

  it("recognizes all document statuses required by the spec", () => {
    expect(documentStatuses).toEqual([
      "queued",
      "processing",
      "indexed",
      "failed",
      "reprocessing",
      "deleting",
      "deleted",
    ]);

    for (const status of documentStatuses) {
      expect(isKnownDocumentStatus(status)).toBe(true);
    }

    expect(documentStatusPresentation("indexed")).toEqual({
      label: "已索引",
      tone: "success",
      known: true,
    });
    expect(documentStatusPresentation("failed")).toEqual({
      label: "处理失败",
      tone: "danger",
      known: true,
    });
  });

  it("returns neutral presentation for unknown document statuses", () => {
    expect(documentStatusPresentation("archived")).toEqual({
      label: "未知状态",
      tone: "neutral",
      known: false,
    });
  });

  it("requests document preview contract by document and chunk id", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input, init) => {
      expect(input).toBe("/backend/documents/doc-a/preview?chunk_id=chunk-a");
      expect(init?.headers).toEqual({ Authorization: "Bearer token-a" });
      return new Response(
        JSON.stringify({
          document_id: "doc-a",
          document_title: "Kubota M7040",
          chunk_id: "chunk-a",
          preview_type: "text",
          accessible: true,
          source_locator: { page: 3 },
          source_position: {
            page_number: 3,
            section_title: "Maintenance",
            worksheet_name: null,
            row_start: null,
            row_end: null,
          },
          evidence_snippet: "Hydraulic maintenance interval.",
          text_preview: "Hydraulic maintenance interval.",
          pdf_page: null,
          highlights: [],
          unavailable_reason: null,
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;

    try {
      const preview = await getDocumentPreview("token-a", "doc-a", "chunk-a");
      expect(preview.preview_type).toBe("text");
      expect(preview.accessible).toBe(true);
      expect(preview.source_position.page_number).toBe(3);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("requests document detail by id with bearer auth", async () => {
    const originalFetch = globalThis.fetch;
    globalThis.fetch = (async (input, init) => {
      expect(input).toBe("/backend/documents/doc-a");
      expect(init?.headers).toEqual({ Authorization: "Bearer token-a" });
      return new Response(
        JSON.stringify({
          id: "doc-a",
          title: "Kubota M7040",
          metadata: {
            brand: "Kubota",
            model: "M7040",
            document_type: "manual",
            language: "zh-CN",
            source: "dealer",
            original_file_name: "m7040.pdf",
            mime_type: "application/pdf",
            file_size_bytes: 2048,
          },
          status: "indexed",
          failure: { stage: null, code: null, message: null },
          recent_task: {
            id: "task-a",
            document_id: "doc-a",
            task_type: "ingest",
            status: "completed",
            attempt_count: 1,
            stage: "indexed",
            error_code: null,
            error_message: null,
            started_at: "2026-06-20T10:00:00Z",
            finished_at: "2026-06-20T10:01:00Z",
          },
          chunks: [{ id: "chunk-a", chunk_type: "text", summary: "Hydraulic", page_number: 3, section_title: "Maintenance" }],
        }),
        { status: 200, headers: { "Content-Type": "application/json" } },
      );
    }) as typeof fetch;

    try {
      const document = await getDocument("token-a", "doc-a");
      expect(document.id).toBe("doc-a");
      expect(document.metadata.original_file_name).toBe("m7040.pdf");
      expect(document.recent_task?.stage).toBe("indexed");
      expect(document.chunks[0]?.page_number).toBe(3);
    } finally {
      globalThis.fetch = originalFetch;
    }
  });

  it("requests chat session CRUD endpoints with bearer auth", async () => {
    const originalFetch = globalThis.fetch;
    const calls: Array<{ input: RequestInfo | URL; init?: RequestInit }> = [];

    globalThis.fetch = (async (input, init) => {
      calls.push({ input, init });
      const method = init?.method ?? "GET";
      const responseBody =
        method === "DELETE"
          ? { session_id: "session-a", deleted: true }
          : {
              id: "session-a",
              title: "液压提升无力",
              messages: [],
              filters: { brand: "Kubota" },
              has_image: false,
              created_at: "2026-06-20T10:00:00Z",
              updated_at: "2026-06-20T10:01:00Z",
            };

      if (input === "/backend/chat-sessions?limit=20") {
        return new Response(JSON.stringify({ total: 1, items: [responseBody] }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }

      return new Response(JSON.stringify(responseBody), {
        status: method === "POST" ? 201 : 200,
        headers: { "Content-Type": "application/json" },
      });
    }) as typeof fetch;

    try {
      await listChatSessions("token-a", 20);
      await createChatSession("token-a", {
        title: "液压提升无力",
        messages: [],
        filters: { brand: "Kubota" },
        has_image: false,
      });
      await getChatSession("token-a", "session-a");
      await updateChatSession("token-a", "session-a", { title: "更新后的会话" });
      await deleteChatSession("token-a", "session-a");

      expect(calls.map((call) => call.input)).toEqual([
        "/backend/chat-sessions?limit=20",
        "/backend/chat-sessions",
        "/backend/chat-sessions/session-a",
        "/backend/chat-sessions/session-a",
        "/backend/chat-sessions/session-a",
      ]);
      expect(calls.map((call) => call.init?.method ?? "GET")).toEqual(["GET", "POST", "GET", "PATCH", "DELETE"]);
      for (const call of calls) {
        expect(call.init?.headers).toEqual(expect.objectContaining({ Authorization: "Bearer token-a" }));
      }
    } finally {
      globalThis.fetch = originalFetch;
    }
  });
});
