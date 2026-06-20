import { describe, expect, it } from "vitest";

import {
  extractAgroMechRequest,
  formatAgroMechAnswer,
} from "./agromech-chat";

describe("agromech chat adapter", () => {
  it("extracts the latest user question and image attachment from UI messages", () => {
    const request = extractAgroMechRequest([
      {
        id: "m1",
        role: "user",
        parts: [{ type: "text", text: "M7040 E01 是什么问题？" }],
      },
      {
        id: "m2",
        role: "assistant",
        parts: [{ type: "text", text: "请补充图片。" }],
      },
      {
        id: "m3",
        role: "user",
        parts: [
          { type: "text", text: "这张仪表盘故障灯怎么处理？" },
          {
            type: "file",
            mediaType: "image/png",
            filename: "dashboard.png",
            url: "data:image/png;base64,aGVsbG8=",
          },
        ],
      },
    ]);

    expect(request).toEqual({
      question: "这张仪表盘故障灯怎么处理？",
      image: {
        dataUrl: "data:image/png;base64,aGVsbG8=",
        filename: "dashboard.png",
        mediaType: "image/png",
      },
    });
  });

  it("formats answers with visual observation, citations, safety warnings and trace id", () => {
    const answer = formatAgroMechAnswer({
      answer: "检查液压泵压力并确认 E01 故障码适用 M7040。",
      sections: { conclusion: "液压系统需检查压力。" },
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
      uncertainty: { level: "low", reasons: [] },
      safety_warnings: ["维修液压系统前先释放高压。"],
      visual_observation: "仪表盘可见液压警告灯。",
    });

    expect(answer).toContain("检查液压泵压力");
    expect(answer).toContain("视觉观察");
    expect(answer).toContain("仪表盘可见液压警告灯");
    expect(answer).toContain("引用来源");
    expect(answer).toContain("M7040 Manual");
    expect(answer).toContain("page: 12");
    expect(answer).toContain("安全提醒");
    expect(answer).toContain("trace-1");
  });
});
