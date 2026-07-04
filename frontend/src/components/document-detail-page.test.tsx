import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DocumentDetailErrorState, DocumentDetailView, documentDetailActionConfirmationContent } from "./document-detail-page";
import type { DocumentDetail } from "@/lib/frontend-api";

function documentDetail(overrides: Partial<DocumentDetail> = {}): DocumentDetail {
  return {
    id: "doc-a",
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
    updated_at: "2026-06-20T10:02:00Z",
    ...overrides,
  };
}

describe("DocumentDetailView", () => {
  it("renders metadata, status, recent task, chunks and action area", () => {
    const html = renderToStaticMarkup(
      <DocumentDetailView
        document={documentDetail()}
        canMutate
        preview={{
          document_id: "doc-a",
          document_title: "Kubota M7040 Manual",
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
          evidence_snippet: "Hydraulic",
          text_preview: "Hydraulic preview text",
          pdf_page: null,
          highlights: [],
          unavailable_reason: null,
        }}
      />,
    );

    expect(html).toContain("Kubota M7040 Manual");
    expect(html).toContain("m7040.pdf");
    expect(html).toContain("入库完成");
    expect(html).toContain("Kubota");
    expect(html).toContain("M7040");
    expect(html).toContain("manual");
    expect(html).toContain("zh-CN");
    expect(html).toContain("2026-06-20T10:02:00Z");
    expect(html).toContain("最近任务");
    expect(html).toContain("task-a");
    expect(html).toContain("Hydraulic");
    expect(html).toContain("原文预览");
    expect(html).toContain("Hydraulic preview text");
    expect(html).toContain("重新处理");
    expect(html).toContain("删除资料");
  });

  it("uses semantic surface and text tokens for dark-mode-ready detail layout", () => {
    const source = readFileSync(new URL("./document-detail-page.tsx", import.meta.url), "utf8");

    expect(source).toContain("bg-surface-panel");
    expect(source).toContain("bg-surface-raised");
    expect(source).toContain("text-foreground");
    expect(source).toContain("text-text-muted");
  });

  it("renders failure and inaccessible states without a blank page", () => {
    const html = renderToStaticMarkup(
      <DocumentDetailView
        document={documentDetail({
          status: "failed",
          failure: { stage: "ocr", code: "ocr_failed", message: "OCR service failed" },
          recent_task: null,
          chunks: [],
        })}
        canMutate={false}
      />,
    );

    expect(html).toContain("处理失败");
    expect(html).toContain("OCR service failed");
    expect(html).toContain("暂无引用预览");
    expect(html).not.toContain("删除资料");
  });

  it("renders retryable detail loading errors", () => {
    const html = renderToStaticMarkup(<DocumentDetailErrorState message="not found" onRetry={() => {}} />);

    expect(html).toContain("资料不可访问");
    expect(html).toContain("not found");
    expect(html).toContain("重试加载");
  });

  it("uses confirmation dialogs for detail mutations", () => {
    const deleteContent = documentDetailActionConfirmationContent("delete", documentDetail());
    const reprocessContent = documentDetailActionConfirmationContent("reprocess", documentDetail());

    expect(deleteContent.title).toBe("确认删除资料");
    expect(deleteContent.description).toContain("m7040.pdf");
    expect(deleteContent.destructive).toBe(true);
    expect(reprocessContent.title).toBe("确认重新处理");
    expect(reprocessContent.description).toContain("会创建新的资料处理任务");
    expect(reprocessContent.destructive).toBe(false);
  });
});
