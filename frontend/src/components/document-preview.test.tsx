import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { DocumentPreviewPanel } from "./document-preview";
import type { DocumentPreviewResponse } from "@/lib/frontend-api";

function preview(overrides: Partial<DocumentPreviewResponse> = {}): DocumentPreviewResponse {
  return {
    document_id: "doc-a",
    document_title: "Kubota M7040 Manual",
    chunk_id: "chunk-a",
    preview_type: "pdf",
    accessible: true,
    source_locator: { page: 3, chunk: "chunk-a" },
    source_position: {
      page_number: 3,
      section_title: "Hydraulic",
      worksheet_name: null,
      row_start: null,
      row_end: null,
    },
    evidence_snippet: "Hydraulic pump pressure should be checked.",
    text_preview: null,
    pdf_page: {
      page_number: 3,
      page_image_url: "/documents/doc-a/assets/page-3",
      render_status: "rendered",
    },
    highlights: [
      {
        type: "area",
        page_number: 3,
        bbox: { x: 0.1, y: 0.2, width: 0.3, height: 0.12 },
      },
    ],
    unavailable_reason: null,
    ...overrides,
  };
}

describe("DocumentPreviewPanel", () => {
  it("renders a PDF page preview with highlight and evidence snippet", () => {
    const html = renderToStaticMarkup(<DocumentPreviewPanel preview={preview()} />);

    expect(html).toContain("原文预览");
    expect(html).toContain("PDF 第 3 页");
    expect(html).toContain("/backend/documents/doc-a/assets/page-3");
    expect(html).toContain("Hydraulic pump pressure");
    expect(html).toContain("left:10%");
    expect(html).toContain("top:20%");
    expect(html).toContain("width:30%");
    expect(html).toContain("height:12%");
  });

  it("renders text preview with source position and text highlight", () => {
    const html = renderToStaticMarkup(
      <DocumentPreviewPanel
        preview={preview({
          preview_type: "text",
          pdf_page: null,
          text_preview: "Hydraulic oil should be replaced every 400 hours.",
          source_position: {
            page_number: null,
            section_title: "Maintenance",
            worksheet_name: "Sheet A",
            row_start: 8,
            row_end: 12,
          },
          highlights: [{ type: "text", text: "400 hours", source_locator: { row: 8 } }],
        })}
      />,
    );

    expect(html).toContain("文本预览");
    expect(html).toContain("Maintenance");
    expect(html).toContain("Sheet A");
    expect(html).toContain("第 8-12 行");
    expect(html).toContain("Hydraulic oil should be replaced");
    expect(html).toContain("400 hours");
  });

  it("shows missing PDF preview data while keeping snippet and locator", () => {
    const html = renderToStaticMarkup(
      <DocumentPreviewPanel
        preview={preview({
          pdf_page: { page_number: 3, page_image_url: null, render_status: "not_rendered" },
          highlights: [],
          unavailable_reason: "pdf_page_file_missing",
        })}
      />,
    );

    expect(html).toContain("PDF 预览数据缺失");
    expect(html).toContain("pdf_page_file_missing");
    expect(html).toContain("Hydraulic pump pressure");
    expect(html).toContain("page: 3");
  });
});
