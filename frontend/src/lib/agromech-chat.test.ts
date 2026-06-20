import { describe, expect, it } from "vitest";

import {
  createAgroMechPayloadDataPart,
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
      filters: {},
      image: {
        dataUrl: "data:image/png;base64,aGVsbG8=",
        filename: "dashboard.png",
        mediaType: "image/png",
      },
    });
  });

  it("cleans filters and preserves session id for text requests", () => {
    const request = extractAgroMechRequest(
      [
        {
          id: "m1",
          role: "user",
          parts: [{ type: "text", text: "E01 液压故障怎么排查？" }],
        },
      ],
      {
        filters: {
          brand: " Kubota ",
          model: "M7040",
          document_type: "",
          language: " zh-CN ",
        },
        session_id: "session-1",
      },
    );

    expect(request).toEqual({
      question: "E01 液压故障怎么排查？",
      filters: {
        brand: "Kubota",
        model: "M7040",
        language: "zh-CN",
      },
      session_id: "session-1",
    });
  });

  it("keeps cleaned filters on image requests", () => {
    const request = extractAgroMechRequest(
      [
        {
          id: "m1",
          role: "user",
          parts: [
            { type: "text", text: "这张图怎么处理？" },
            {
              type: "file",
              mediaType: "image/webp",
              filename: "fault.webp",
              url: "data:image/webp;base64,aGVsbG8=",
            },
          ],
        },
      ],
      {
        filters: {
          brand: "",
          model: " L3901 ",
          document_type: "manual",
          language: "",
        },
      },
    );

    expect(request).toEqual({
      question: "这张图怎么处理？",
      filters: {
        model: "L3901",
        document_type: "manual",
      },
      image: {
        dataUrl: "data:image/webp;base64,aGVsbG8=",
        filename: "fault.webp",
        mediaType: "image/webp",
      },
    });
  });

  it("formats only the main answer while structured fields stay in the data part", () => {
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

    expect(answer).toBe("检查液压泵压力并确认 E01 故障码适用 M7040。");
    expect(answer).not.toContain("视觉观察");
    expect(answer).not.toContain("引用来源");
    expect(answer).not.toContain("安全提醒");
    expect(answer).not.toContain("trace-1");
  });

  it("creates a data part that preserves the structured qa payload", () => {
    const dataPart = createAgroMechPayloadDataPart({
      answer: "检查液压泵压力。",
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
      ocr_text: "E01",
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
      question_image: {
        dataUrl: "data:image/png;base64,aGVsbG8=",
        filename: "dashboard.png",
        mediaType: "image/png",
      },
    });

    expect(dataPart).toEqual({
      type: "data-agromech-payload",
      id: "agromech-payload",
      data: {
        answer: "检查液压泵压力。",
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
        ocr_text: "E01",
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
        question_image: {
          dataUrl: "data:image/png;base64,aGVsbG8=",
          filename: "dashboard.png",
          mediaType: "image/png",
        },
      },
    });
  });

  it("uses stable defaults when structured payload fields are missing", () => {
    const dataPart = createAgroMechPayloadDataPart({
      answer: "未找到足够来源证据。",
      uncertainty: { level: "high", reasons: ["evidence_insufficient"] },
      safety_warnings: [],
    });

    expect(dataPart.data.citations).toEqual([]);
    expect(dataPart.data.trace_id).toBeNull();
    expect(dataPart.data.sections).toEqual({});
    expect(dataPart.data.safety_warnings).toEqual([]);
    expect(dataPart.data.uncertainty).toEqual({
      level: "high",
      reasons: ["evidence_insufficient"],
    });
  });
});
