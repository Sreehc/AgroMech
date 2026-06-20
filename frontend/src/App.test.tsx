import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, test, vi } from "vitest";

import App from "./App";

const okLogin = {
  access_token: "token-admin",
  token_type: "bearer",
  expires_in: 3600
};

function mockFetch(handler: (input: RequestInfo | URL, init?: RequestInit) => Response | Promise<Response>) {
  vi.stubGlobal("fetch", vi.fn(handler));
}

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" }
  });
}

describe("App authentication", () => {
  beforeEach(() => {
    localStorage.clear();
    window.history.pushState({}, "", "/");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  test("disables login while username or password is empty", async () => {
    const user = userEvent.setup();

    render(<App />);

    const button = screen.getByRole("button", { name: "登录" });
    expect(button).toBeDisabled();

    await user.type(screen.getByLabelText("账号"), "admin");
    expect(button).toBeDisabled();
  });

  test("keeps username and clears password after failed login", async () => {
    mockFetch(() =>
      jsonResponse(
        {
          error: {
            code: "unauthorized",
            message: "Invalid username or password",
            details: null,
            trace_id: "trace"
          }
        },
        401
      )
    );
    const user = userEvent.setup();

    render(<App />);

    await user.type(screen.getByLabelText("账号"), "admin");
    await user.type(screen.getByLabelText("密码"), "wrong");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByText("登录已失效，请重新登录。")).toBeInTheDocument();
    expect(screen.getByLabelText("账号")).toHaveValue("admin");
    expect(screen.getByLabelText("密码")).toHaveValue("");
  });

  test("redirects unauthenticated users to login", () => {
    window.history.pushState({}, "", "/qa");

    render(<App />);

    expect(screen.getByRole("heading", { name: "登录" })).toBeInTheDocument();
    expect(window.location.pathname).toBe("/login");
  });

  test("logs in and renders the protected workspace", async () => {
    mockFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/login")) {
        return jsonResponse(okLogin);
      }
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    render(<App />);

    await user.type(screen.getByLabelText("账号"), "admin");
    await user.type(screen.getByLabelText("密码"), "secret");
    await user.click(screen.getByRole("button", { name: "登录" }));

    expect(await screen.findByRole("heading", { name: "问答" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "资料库" })).toBeInTheDocument();
  });

  test("hides maintenance navigation for users without permission", async () => {
    localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "token-user", username: "readonly", role: "user" })
    );
    mockFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "readonly", role: "user" });
      }
      return jsonResponse({}, 404);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "问答" });
    expect(screen.queryByRole("link", { name: "资料库" })).not.toBeInTheDocument();
  });

  test("clears expired sessions and returns to login", async () => {
    localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "expired", username: "admin", role: "admin" })
    );
    mockFetch(() =>
      jsonResponse(
        {
          error: {
            code: "unauthorized",
            message: "Invalid or expired access token",
            details: null,
            trace_id: "trace"
          }
        },
        401
      )
    );

    render(<App />);

    await waitFor(() => expect(window.location.pathname).toBe("/login"));
    expect(localStorage.getItem("agromech.session")).toBeNull();
    expect(screen.getByRole("heading", { name: "登录" })).toBeInTheDocument();
  });
});

describe("Document library", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "token-admin", username: "admin", role: "admin" })
    );
    window.history.pushState({}, "", "/library");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  test("lists documents with status metadata and row actions", async () => {
    mockFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      if (url.includes("/documents")) {
        return jsonResponse({
          total: 1,
          items: [
            {
              id: "doc-1",
              title: "M7040 Manual",
              original_file_name: "m7040.pdf",
              brand: "Kubota",
              model: "M7040",
              document_type: "manual",
              language: "zh-CN",
              status: "indexed",
              updated_at: "2026-06-20T08:00:00"
            }
          ]
        });
      }
      return jsonResponse({}, 404);
    });

    render(<App />);

    expect(await screen.findByRole("heading", { name: "资料库" })).toBeInTheDocument();
    expect(await screen.findByText("M7040 Manual")).toBeInTheDocument();
    expect(screen.getByText("Kubota / M7040")).toBeInTheDocument();
    expect(screen.getByText("indexed")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "重新处理 M7040 Manual" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "删除 M7040 Manual" })).toBeInTheDocument();
  });

  test("uploads a document and shows document and task ids", async () => {
    mockFetch((input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      if (url.endsWith("/documents") && init?.method === "POST") {
        return jsonResponse({ document_id: "doc-new", task_id: "task-new", status: "queued" }, 201);
      }
      if (url.includes("/documents")) {
        return jsonResponse({ total: 0, items: [] });
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    render(<App />);

    await screen.findByRole("heading", { name: "资料库" });
    await user.upload(
      screen.getByLabelText("选择资料文件"),
      new File(["manual"], "manual.txt", { type: "text/plain" })
    );
    await user.click(screen.getByRole("button", { name: "上传资料" }));

    expect(await screen.findByText("document_id: doc-new")).toBeInTheDocument();
    expect(screen.getByText("task_id: task-new")).toBeInTheDocument();
  });

  test("shows duplicate upload dialog with cancel and continue actions", async () => {
    mockFetch((input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      if (url.endsWith("/documents") && init?.method === "POST") {
        return jsonResponse(
          {
            error: {
              code: "duplicate_of",
              message: "Duplicate file",
              details: { document_id: "doc-existing" },
              trace_id: "trace"
            }
          },
          409
        );
      }
      if (url.includes("/documents")) {
        return jsonResponse({ total: 0, items: [] });
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    render(<App />);

    await screen.findByRole("heading", { name: "资料库" });
    await user.upload(
      screen.getByLabelText("选择资料文件"),
      new File(["manual"], "manual.txt", { type: "text/plain" })
    );
    await user.click(screen.getByRole("button", { name: "上传资料" }));

    expect(await screen.findByRole("dialog", { name: "重复资料" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "取消" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "继续上传为新版本" })).toBeInTheDocument();
  });

  test("confirms reprocess and delete actions", async () => {
    const calls: string[] = [];
    mockFetch((input, init) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      if (url.endsWith("/documents/doc-1/reprocess")) {
        return jsonResponse({ document_id: "doc-1", task_id: "task-reprocess", status: "queued" }, 201);
      }
      if (url.endsWith("/documents/doc-1") && init?.method === "DELETE") {
        return jsonResponse({ document_id: "doc-1", status: "deleted" });
      }
      if (url.includes("/documents")) {
        return jsonResponse({
          total: 1,
          items: [
            {
              id: "doc-1",
              title: "M7040 Manual",
              original_file_name: "m7040.pdf",
              brand: "Kubota",
              model: "M7040",
              document_type: "manual",
              language: "zh-CN",
              status: "indexed",
              updated_at: "2026-06-20T08:00:00"
            }
          ]
        });
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    render(<App />);

    await screen.findByText("M7040 Manual");
    await user.click(screen.getByRole("button", { name: "重新处理 M7040 Manual" }));
    expect(await screen.findByRole("dialog", { name: "确认重新处理" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认重新处理" }));
    expect(await screen.findByText("task_id: task-reprocess")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "删除 M7040 Manual" }));
    expect(await screen.findByRole("dialog", { name: "确认删除" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "确认删除" }));

    await waitFor(() => expect(calls.some((call) => call.includes("DELETE") && call.includes("/documents/doc-1"))).toBe(true));
  });
});

describe("Question answering page", () => {
  beforeEach(() => {
    localStorage.clear();
    localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "token-admin", username: "admin", role: "admin" })
    );
    window.history.pushState({}, "", "/qa");
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    localStorage.clear();
  });

  test("disables submit while question is empty", async () => {
    mockFetch((input) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      return jsonResponse({}, 404);
    });

    render(<App />);

    await screen.findByRole("heading", { name: "问答" });
    expect(screen.getByRole("button", { name: "提交问题" })).toBeDisabled();
  });

  test("submits text question and renders answer citations and trace", async () => {
    mockFetch((input, init) => {
      const url = String(input);
      if (url.endsWith("/auth/me")) {
        return jsonResponse({ username: "admin", role: "admin" });
      }
      if (url.endsWith("/qa/text") && init?.method === "POST") {
        return jsonResponse({
          answer: "根据证据，检查液压泵压力。",
          sections: {
            conclusion: "检查液压泵压力。",
            applicability: "适用 M7040。",
            possible_causes: ["液压泵压力不足"],
            inspection_steps: ["核对压力表"],
            safety_reminder: ["停机并释放液压压力"],
            citations: ["M7040 Manual / chunk-m7040"],
            uncertainty: { level: "low", reasons: [] }
          },
          citations: [
            {
              document_id: "doc-m7040",
              document_title: "M7040 Manual",
              chunk_id: "chunk-m7040",
              source_locator: { page: 3 },
              evidence_snippet: "Hydraulic pump pressure.",
              evidence_type: "text",
              accessible: true
            }
          ],
          trace_id: "trace-answer",
          uncertainty: { level: "low", reasons: [] },
          safety_warnings: ["停机并释放液压压力"]
        });
      }
      if (url.endsWith("/retrieval-traces/trace-answer")) {
        return jsonResponse({
          trace_id: "trace-answer",
          query: "M7040 E01",
          filters: { model: "M7040" },
          channels: { used: ["keyword", "vector"], degraded: [{ channel: "graph", reason: "timeout" }] },
          candidates: [{ chunk_id: "chunk-m7040", score: 4.2, channels: ["keyword"] }],
          rerank: { items: [{ chunk_id: "chunk-m7040", before_rank: 2, after_rank: 1 }] },
          final_evidence: [{ chunk_id: "chunk-m7040", document_id: "doc-m7040" }]
        });
      }
      return jsonResponse({}, 404);
    });
    const user = userEvent.setup();

    render(<App />);

    await screen.findByRole("heading", { name: "问答" });
    await user.type(screen.getByLabelText("问题"), "M7040 E01 怎么排查？");
    await user.click(screen.getByRole("button", { name: "提交问题" }));

    expect(await screen.findByText("retrieving/generating")).toBeInTheDocument();
    expect(await screen.findByText("检查液压泵压力。")).toBeInTheDocument();
    expect(screen.getByText("M7040 Manual")).toBeInTheDocument();
    expect(screen.getByText("page: 3")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "查看检索链路" }));
    expect(await screen.findByText("trace-answer")).toBeInTheDocument();
    expect(screen.getByText("keyword, vector")).toBeInTheDocument();
    expect(screen.getByText("graph: timeout")).toBeInTheDocument();
    expect(screen.getByText("chunk-m7040 2 -> 1")).toBeInTheDocument();
  });
});
