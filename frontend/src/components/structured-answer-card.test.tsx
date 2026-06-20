import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { StructuredAnswerCard } from "./structured-answer-card";
import type { AgroMechStructuredPayload } from "@/lib/agromech-chat";

function payload(overrides: Partial<AgroMechStructuredPayload> = {}): AgroMechStructuredPayload {
  return {
    answer: "检查液压泵压力并确认 E01 故障码适用 M7040。",
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
    ],
    trace_id: "trace-1",
    uncertainty: { level: "low", reasons: ["证据匹配型号"] },
    safety_warnings: ["维修液压系统前先释放高压。"],
    visual_observation: "仪表盘可见液压警告灯。",
    ocr_text: "E01",
    ...overrides,
  };
}

describe("StructuredAnswerCard", () => {
  it("renders structured safety, uncertainty, OCR, visual observation, and citation entry", () => {
    const html = renderToStaticMarkup(<StructuredAnswerCard payload={payload()} />);

    expect(html).toContain("检查液压泵压力");
    expect(html).toContain("安全提醒");
    expect(html).toContain("维修液压系统前先释放高压");
    expect(html).toContain("不确定性");
    expect(html).toContain("low");
    expect(html).toContain("视觉观察");
    expect(html).toContain("仪表盘可见液压警告灯");
    expect(html).toContain("OCR");
    expect(html).toContain("E01");
    expect(html).toContain("引用来源");
    expect(html).toContain("M7040 Manual");
    expect(html).toContain("查看证据");
  });

  it("renders uploaded image thumbnail and visual annotations when available", () => {
    const html = renderToStaticMarkup(
      <StructuredAnswerCard
        payload={payload({
          question_image: {
            dataUrl: "data:image/png;base64,aGVsbG8=",
            filename: "dashboard.png",
            mediaType: "image/png",
          },
          visual_annotations: [
            {
              id: "warning-light-1",
              type: "warning_light",
              label: "E01",
              confidence: 0.8,
              bbox: { format: "normalized_xywh", x: 0.62, y: 0.12, width: 0.18, height: 0.16 },
            },
          ],
          visual_annotation_status: {
            status: "available",
            coordinate_format: "normalized_xywh",
            missing_reason: null,
          },
        })}
      />,
    );

    expect(html).toContain("现场图片");
    expect(html).toContain("dashboard.png");
    expect(html).toContain("E01");
    expect(html).toContain("80%");
    expect(html).toContain("left:62%");
  });

  it("shows a stable no-citation state", () => {
    const html = renderToStaticMarkup(<StructuredAnswerCard payload={payload({ citations: [] })} />);

    expect(html).toContain("未返回可引用来源");
  });
});
