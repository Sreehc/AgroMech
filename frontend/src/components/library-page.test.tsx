import { readFileSync } from "node:fs";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  LibraryDocumentList,
  LibraryErrorAlert,
  LibraryStatusOverview,
  libraryActionConfirmationContent,
} from "./library-page";
import type { DocumentSummary } from "@/lib/frontend-api";

const documents: DocumentSummary[] = [
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
    recent_task: {
      id: "task-1",
      task_type: "ingest",
      status: "completed",
      stage: "indexed",
    },
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
    recent_task: {
      id: "task-2",
      task_type: "ingest",
      status: "failed",
      stage: "ocr",
    },
    failure: {
      stage: "ocr",
      code: "ocr_failed",
      message: "OCR service failed",
    },
  },
];

describe("library document list", () => {
  it("raises the dev proxy body limit for large manual uploads", () => {
    const nextConfig = readFileSync(
      new URL("../../next.config.ts", import.meta.url),
      "utf8",
    );

    expect(nextConfig).toContain("proxyClientMaxBodySize");
    expect(nextConfig).toContain('"120mb"');
  });

  it("shows structured filter controls with select-friendly labels", () => {
    const source = readFileSync(
      new URL("./library-page.tsx", import.meta.url),
      "utf8",
    );

    expect(source).toContain(
      "lg:grid-cols-[minmax(320px,380px)_minmax(0,1fr)]",
    );
    expect(source).toContain("上传与处理");
    expect(source).toContain("资料浏览");
    expect(source).toContain("rounded-2xl border border-border");
    expect(source).toContain("bg-surface-panel/65");
    expect(source).toContain("divide-y divide-border/80");
    expect(source).toContain("bg-surface-raised/85");
    expect(source).toContain("bg-surface-panel/40");
    expect(source).toContain("选择品牌或直接输入");
    expect(source).toContain("选择型号或直接输入");
    expect(source).toContain("可直接输入品牌或型号；没有匹配项时按回车即可。");
    expect(source).toContain("const hasDraftFilters");
    expect(source).toContain("重置");
    expect(source).toContain("statusOptions");
    expect(source).toContain("languageOptions");
    expect(source).not.toContain(">清空筛选<");
    expect(source).not.toContain("bg-white/78");
    expect(source).not.toContain("bg-white/40");
  });

  it("renders scan-friendly fields and status overview", () => {
    const overviewHtml = renderToStaticMarkup(
      <LibraryStatusOverview total={2} documents={documents} />,
    );
    const listHtml = renderToStaticMarkup(
      <LibraryDocumentList
        canMutate
        documents={documents}
        expandedDocumentId={null}
        loading={false}
        total={2}
        onDelete={() => {}}
        onExpand={() => {}}
        onReprocess={() => {}}
      />,
    );

    expect(overviewHtml).toContain("总资料数");
    expect(overviewHtml).toContain("已索引");
    expect(overviewHtml).toContain("失败");
    expect(overviewHtml).toContain("当前资料状态");
    expect(listHtml).toContain("M7040 Hydraulic Manual");
    expect(listHtml).toContain("m7040-hydraulic.pdf");
    expect(listHtml).toContain("Kubota / M7040");
    expect(listHtml).toContain("manual / zh-CN");
    expect(listHtml).toContain("已索引");
    expect(listHtml).toContain("查看详情");
    expect(listHtml).toContain("展开");
  });

  it("renders expanded summary, recent task, failure and quick actions", () => {
    const html = renderToStaticMarkup(
      <LibraryDocumentList
        canMutate
        documents={documents}
        expandedDocumentId="doc-failed"
        loading={false}
        total={2}
        onDelete={() => {}}
        onExpand={() => {}}
        onReprocess={() => {}}
      />,
    );

    expect(html).toContain("列表内摘要");
    expect(html).toContain("最近任务");
    expect(html).toContain("task-2");
    expect(html).toContain("ocr_failed");
    expect(html).toContain("OCR service failed");
    expect(html).toContain("重新处理");
    expect(html).toContain("删除");
  });

  it("renders filter empty state with clear action", () => {
    const html = renderToStaticMarkup(
      <LibraryDocumentList
        canMutate={false}
        documents={[]}
        expandedDocumentId={null}
        loading={false}
        total={0}
        hasActiveFilters
        onClearFilters={() => {}}
        onDelete={() => {}}
        onExpand={() => {}}
        onReprocess={() => {}}
      />,
    );

    expect(html).toContain("筛选无结果");
    expect(html).toContain("清空筛选");
  });

  it("renders the upload workspace summary for current processing context", () => {
    const source = readFileSync(
      new URL("./library-page.tsx", import.meta.url),
      "utf8",
    );

    expect(source).toContain(
      "先添加文件，再点击“开始上传”。",
    );
    expect(source).toContain("最近动态");
    expect(source).toContain("上传队列");
  });

  it("hides mutation actions for read-only users", () => {
    const html = renderToStaticMarkup(
      <LibraryDocumentList
        canMutate={false}
        documents={documents}
        expandedDocumentId="doc-failed"
        loading={false}
        total={2}
        onDelete={() => {}}
        onExpand={() => {}}
        onReprocess={() => {}}
      />,
    );

    expect(html).toContain("查看详情");
    expect(html).not.toContain("重新处理");
    expect(html).not.toContain("删除");
  });

  it("renders retryable list loading errors", () => {
    const html = renderToStaticMarkup(
      <LibraryErrorAlert message="network unavailable" onRetry={() => {}} />,
    );

    expect(html).toContain("资料列表加载失败");
    expect(html).toContain("network unavailable");
    expect(html).toContain("重试加载");
  });

  it("uses confirmation dialogs for destructive library actions", () => {
    const deleteContent = libraryActionConfirmationContent({
      action: "delete",
      document: documents[0],
    });
    const reprocessContent = libraryActionConfirmationContent({
      action: "reprocess",
      document: documents[0],
    });

    expect(deleteContent.title).toBe("确认删除资料");
    expect(deleteContent.description).toContain("删除后历史引用只能保留元数据");
    expect(deleteContent.destructive).toBe(true);
    expect(reprocessContent.title).toBe("确认重新处理");
    expect(reprocessContent.description).toContain("会创建新的资料处理任务");
    expect(reprocessContent.destructive).toBe(false);
  });
});
