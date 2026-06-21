import { mkdirSync } from "node:fs";
import { join } from "node:path";

import { expect, test, type Page } from "@playwright/test";

const screenshotDir = join(process.cwd(), "test-results", "agromech-screenshots");

test.beforeAll(() => {
  mkdirSync(screenshotDir, { recursive: true });
});

test.beforeEach(async ({ page }) => {
  await mockBackend(page);
});

test("captures login, workbench, library, and detail screenshots", async ({ page }, testInfo) => {
  await captureLogin(page, testInfo.project.name, "light");
  await captureLogin(page, testInfo.project.name, "dark");

  await installSession(page, "light");
  await page.goto("/");
  await expect(page.getByText("农机维修问答工作台")).toBeVisible();
  await expect(page.getByText("会话历史")).toBeVisible();
  await expect(page.getByText("资料上下文")).toBeVisible();
  await capture(page, `${testInfo.project.name}-workbench-light.png`);

  await installSession(page, "dark");
  await page.goto("/library");
  await expect(page.locator("header").filter({ hasText: "Library" }).getByRole("heading", { name: "资料库" })).toBeVisible();
  await expect(page.getByText("M7040 Hydraulic Manual")).toBeVisible();
  await capture(page, `${testInfo.project.name}-library-dark.png`);

  await page.goto("/library/doc-indexed");
  await expect(page.getByRole("heading", { name: "Kubota M7040 Manual" })).toBeVisible();
  await expect(page.getByText("原文预览")).toBeVisible();
  await capture(page, `${testInfo.project.name}-detail-dark.png`);
});

async function captureLogin(page: Page, projectName: string, theme: "light" | "dark") {
  await installTheme(page, theme);
  await page.goto("/login");
  await expect(page.getByRole("heading", { name: "农机维修 AI 资料工作台" })).toBeVisible();
  await expect(page.getByText("服务状态")).toBeVisible();
  await capture(page, `${projectName}-login-${theme}.png`);
}

async function capture(page: Page, fileName: string) {
  await expect(page.locator("body")).toBeVisible();
  await expect.poll(async () => page.locator("body").innerText()).not.toMatch(/\b(undefined|null)\b/);
  await page.screenshot({ fullPage: true, path: join(screenshotDir, fileName) });
}

async function installTheme(page: Page, theme: "light" | "dark") {
  await page.addInitScript((themeValue) => {
    window.localStorage.setItem("agromech.theme", themeValue);
  }, theme);
}

async function installSession(page: Page, theme: "light" | "dark") {
  await page.addInitScript((themeValue) => {
    window.localStorage.setItem("agromech.theme", themeValue);
    window.localStorage.setItem(
      "agromech.session",
      JSON.stringify({ token: "e2e-token", username: "admin", role: "admin" }),
    );
    window.localStorage.setItem(
      "agromech.chat_sessions.v1.admin",
      JSON.stringify({
        version: 1,
        username: "admin",
        sessions: [
          {
            id: "session-e2e",
            title: "液压提升无力",
            messages: [{ role: "user", content: "怎么排查" }],
            filters: { brand: "Kubota", model: "M7040" },
            has_image: true,
            created_at: "2026-06-20T10:00:00Z",
            updated_at: "2026-06-20T10:01:00Z",
          },
        ],
      }),
    );
  }, theme);
}

async function mockBackend(page: Page) {
  await page.route("**/backend/chat-sessions?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        total: 1,
        items: [
          {
            id: "session-e2e",
            title: "液压提升无力",
            messages: [{ role: "user", content: "怎么排查" }],
            filters: { brand: "Kubota", model: "M7040" },
            has_image: true,
            created_at: "2026-06-20T10:00:00Z",
            updated_at: "2026-06-20T10:01:00Z",
          },
        ],
      }),
    });
  });

  await page.route(/\/backend\/documents(\?.*)?$/, async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        total: 2,
        items: [
          {
            id: "doc-indexed",
            title: "M7040 Hydraulic Manual",
            original_file_name: "m7040-hydraulic.pdf",
            brand: "Kubota",
            model: "M7040",
            document_type: "manual",
            language: "zh-CN",
            status: "indexed",
            updated_at: "2026-06-20T10:00:00Z",
            summary: "Hydraulic service procedure",
            recent_task: { id: "task-1", task_type: "ingest", status: "completed", stage: "indexed" },
            failure: { stage: null, code: null, message: null },
          },
          {
            id: "doc-failed",
            title: "L3901 Fault Codes",
            original_file_name: "l3901-faults.pdf",
            brand: "Kubota",
            model: "L3901",
            document_type: "fault-code",
            language: "en-US",
            status: "failed",
            updated_at: null,
            summary: null,
            recent_task: { id: "task-2", task_type: "ingest", status: "failed", stage: "ocr" },
            failure: { stage: "ocr", code: "ocr_failed", message: "OCR service failed" },
          },
        ],
      }),
    });
  });

  await page.route("**/backend/documents/doc-indexed", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        id: "doc-indexed",
        title: "Kubota M7040 Manual",
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
          document_id: "doc-indexed",
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
        updated_at: "2026-06-20T10:02:00Z",
      }),
    });
  });

  await page.route("**/backend/documents/doc-indexed/preview?**", async (route) => {
    await route.fulfill({
      contentType: "application/json",
      body: JSON.stringify({
        document_id: "doc-indexed",
        document_title: "Kubota M7040 Manual",
        chunk_id: "chunk-a",
        preview_type: "text",
        accessible: true,
        source_locator: { page: 3 },
        source_position: {
          page_number: 3,
          section_title: "Hydraulic",
          worksheet_name: null,
          row_start: null,
          row_end: null,
        },
        evidence_snippet: "Hydraulic pump pressure should be checked.",
        text_preview: "Hydraulic pump pressure should be checked before replacing parts.",
        pdf_page: null,
        highlights: [{ type: "text", text: "Hydraulic pump pressure", source_locator: { page: 3 } }],
        unavailable_reason: null,
      }),
    });
  });
}
