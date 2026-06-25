import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { EvidencePanel } from "./evidence-panel";
import type { AgroMechStructuredPayload } from "@/lib/agromech-chat";

function payload(
  overrides: Partial<AgroMechStructuredPayload> = {},
): AgroMechStructuredPayload {
  return {
    answer: "检查液压系统。",
    sections: {},
    citations: [
      {
        document_id: "doc-1",
        document_title: "M7040 Manual",
        chunk_id: "chunk-1",
        source_locator: { page: 12 },
        evidence_snippet: "E01 hydraulic pump pressure",
        evidence_type: "text",
        accessible: true,
      },
      {
        document_id: "doc-2",
        document_title: "Deleted Manual",
        chunk_id: "chunk-2",
        source_locator: { page: 3 },
        evidence_snippet: "source removed",
        evidence_type: "text",
        accessible: false,
      },
    ],
    trace_id: "trace-1",
    uncertainty: { level: "medium", reasons: ["型号可能不完全一致"] },
    safety_warnings: ["释放液压压力后再维修。"],
    ...overrides,
  };
}

describe("EvidencePanel", () => {
  it("renders selected citation details, trace, uncertainty, safety warning and document link", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel payload={payload()} activeIndex={0} onSelect={() => {}} />,
    );

    expect(html).toContain("证据面板");
    expect(html).toContain("M7040 Manual");
    expect(html).toContain("E01 hydraulic pump pressure");
    expect(html).toContain("page: 12");
    expect(html).toContain("Trace ID");
    expect(html).toContain("trace-1");
    expect(html).toContain("复制");
    expect(html).toContain("Trace 摘要");
    expect(html).toContain("可查看本次检索使用的证据");
    expect(html).toContain("不确定性");
    expect(html).toContain("medium");
    expect(html).toContain("型号可能不完全一致");
    expect(html).toContain("安全提醒");
    expect(html).toContain("释放液压压力后再维修");
    expect(html).toContain("/library/doc-1");
    expect(html).toContain(
      "rounded-2xl border border-border bg-surface-panel/65",
    );
    expect(html).toContain(
      "rounded-xl border border-border/70 bg-surface-raised/85",
    );
    expect(html).not.toContain("bg-white/78");
  });

  it("renders an accessible close control when the workbench opens the panel", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel payload={payload()} activeIndex={0} onClose={() => {}} />,
    );

    expect(html).toContain("关闭证据面板");
  });

  it("can render source preview for the selected citation", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel
        payload={payload()}
        activeIndex={0}
        preview={{
          document_id: "doc-1",
          document_title: "M7040 Manual",
          chunk_id: "chunk-1",
          preview_type: "text",
          accessible: true,
          source_locator: { page: 12 },
          source_position: {
            page_number: 12,
            section_title: "Hydraulic",
            worksheet_name: null,
            row_start: null,
            row_end: null,
          },
          evidence_snippet: "E01 hydraulic pump pressure",
          text_preview: "E01 hydraulic pump pressure",
          pdf_page: null,
          highlights: [],
          unavailable_reason: null,
        }}
      />,
    );

    expect(html).toContain("原文预览");
    expect(html).toContain("E01 hydraulic pump pressure");
  });

  it("shows inaccessible source state and keeps citation metadata", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel payload={payload()} activeIndex={1} onSelect={() => {}} />,
    );

    expect(html).toContain("Deleted Manual");
    expect(html).toContain("source removed");
    expect(html).toContain("来源不可访问");
  });

  it("keeps mobile sheet and close wiring in the workbench evidence region", () => {
    const workbench = readFileSync(
      new URL("./assistant-workbench.tsx", import.meta.url),
      "utf8",
    );

    expect(workbench).toContain("max-xl:fixed");
    expect(workbench).toContain("max-xl:bottom-3");
    expect(workbench).toContain(
      "onClose={selectedEvidence ? onEvidenceClose : undefined}",
    );
  });

  it("renders a stable empty state when no citations are available", () => {
    const html = renderToStaticMarkup(
      <EvidencePanel payload={payload({ citations: [] })} activeIndex={0} />,
    );

    expect(html).toContain("未返回结构化证据");
  });
});
