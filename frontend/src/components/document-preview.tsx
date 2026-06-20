/* eslint-disable @next/next/no-img-element */

import { FileText } from "@phosphor-icons/react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import type { DocumentPreviewHighlight, DocumentPreviewResponse } from "@/lib/frontend-api";

export function DocumentPreviewPanel({ preview }: { preview: DocumentPreviewResponse }) {
  const sourceLabel = formatSourcePosition(preview);

  return (
    <section className="grid gap-3 rounded-lg border border-border bg-surface-panel p-4" data-document-preview>
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-[0.16em] text-text-muted">Source Preview</p>
          <h2 className="text-base font-semibold text-foreground">原文预览</h2>
          <p className="mt-1 text-sm text-text-muted">{sourceLabel}</p>
        </div>
        <Badge tone={preview.accessible ? "success" : "danger"}>{preview.accessible ? "来源可访问" : "来源不可访问"}</Badge>
      </div>

      {preview.evidence_snippet ? (
        <div className="rounded-lg border border-border bg-surface-raised p-3">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-text-muted">证据片段</p>
          <p className="mt-2 text-sm leading-6 text-foreground">{preview.evidence_snippet}</p>
        </div>
      ) : null}

      {preview.preview_type === "pdf" ? <PdfPreview preview={preview} /> : null}
      {preview.preview_type === "text" ? <TextPreview preview={preview} /> : null}
      {preview.preview_type === "unavailable" ? <UnavailablePreview preview={preview} /> : null}
    </section>
  );
}

function PdfPreview({ preview }: { preview: DocumentPreviewResponse }) {
  const pdfPage = preview.pdf_page;
  const pageUrl = normalizeBackendAssetUrl(pdfPage?.page_image_url ?? null);

  if (!pdfPage || !pageUrl || pdfPage.render_status !== "rendered") {
    return (
      <Alert tone="warning">
        <AlertTitle>PDF 预览数据缺失</AlertTitle>
        <AlertDescription>
          {[preview.unavailable_reason, pdfPage?.render_status ? `render_status: ${pdfPage.render_status}` : null, formatLocator(preview.source_locator)]
            .filter(Boolean)
            .join("；")}
        </AlertDescription>
      </Alert>
    );
  }

  return (
    <div className="grid gap-2">
      <div className="flex flex-wrap items-center justify-between gap-2 text-sm">
        <span className="font-medium text-foreground">PDF 第 {pdfPage.page_number ?? preview.source_position.page_number ?? "未知"} 页</span>
        <span className="text-xs text-text-muted">高亮区域仅用于定位引用证据。</span>
      </div>
      <figure className="relative overflow-hidden rounded-lg border border-border bg-surface-raised">
        <img className="block w-full bg-white" src={pageUrl} alt={`${preview.document_title} PDF 页面预览`} />
        {preview.highlights.filter(isAreaHighlight).map((highlight, index) => (
          <span
            aria-hidden="true"
            className="absolute rounded-sm border-2 border-status-warning bg-status-warning/25 shadow-[0_0_0_9999px_rgba(0,0,0,0.02)]"
            key={`${highlight.page_number ?? "page"}-${index}`}
            style={bboxStyle(highlight)}
          />
        ))}
      </figure>
    </div>
  );
}

function TextPreview({ preview }: { preview: DocumentPreviewResponse }) {
  const textHighlights = preview.highlights.filter((highlight) => highlight.type === "text" && highlight.text);

  return (
    <div className="grid gap-3">
      <div className="flex items-center gap-2 text-sm font-medium text-foreground">
        <FileText className="size-4 text-primary" />
        文本预览
      </div>
      <pre className="max-h-80 overflow-auto whitespace-pre-wrap rounded-lg border border-border bg-surface-raised p-3 text-sm leading-6 text-foreground">
        {preview.text_preview || preview.evidence_snippet || "未返回文本预览。"}
      </pre>
      {textHighlights.length ? (
        <div className="grid gap-2">
          <p className="text-xs font-semibold uppercase tracking-[0.12em] text-text-muted">文本高亮</p>
          {textHighlights.map((highlight, index) => (
            <p className="rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2 text-sm text-status-warning" key={index}>
              {highlight.text}
            </p>
          ))}
        </div>
      ) : null}
    </div>
  );
}

function UnavailablePreview({ preview }: { preview: DocumentPreviewResponse }) {
  return (
    <Alert tone="danger">
      <AlertTitle>原文不可访问</AlertTitle>
      <AlertDescription>
        {[preview.unavailable_reason || "来源不可访问", formatLocator(preview.source_locator)].filter(Boolean).join("；")}
      </AlertDescription>
    </Alert>
  );
}

function isAreaHighlight(highlight: DocumentPreviewHighlight): highlight is DocumentPreviewHighlight & {
  bbox: { x: number; y: number; width: number; height: number };
} {
  return highlight.type === "area" && Boolean(highlight.bbox);
}

function bboxStyle(highlight: { bbox: { x: number; y: number; width: number; height: number } }) {
  return {
    left: `${asPercent(highlight.bbox.x)}%`,
    top: `${asPercent(highlight.bbox.y)}%`,
    width: `${asPercent(highlight.bbox.width)}%`,
    height: `${asPercent(highlight.bbox.height)}%`,
  };
}

function asPercent(value: number): number {
  const percent = value <= 1 ? value * 100 : value;
  return Math.max(0, Math.min(100, Math.round(percent * 100) / 100));
}

function normalizeBackendAssetUrl(url: string | null): string | null {
  if (!url) return null;
  if (url.startsWith("/documents/")) return `/backend${url}`;
  return url;
}

function formatSourcePosition(preview: DocumentPreviewResponse): string {
  const parts = [
    preview.source_position.section_title,
    preview.source_position.page_number ? `第 ${preview.source_position.page_number} 页` : null,
    preview.source_position.worksheet_name,
    formatRows(preview.source_position.row_start, preview.source_position.row_end),
  ].filter(Boolean);

  return parts.join(" · ") || formatLocator(preview.source_locator) || "来源定位不可用";
}

function formatRows(rowStart: number | null, rowEnd: number | null): string | null {
  if (rowStart === null && rowEnd === null) return null;
  if (rowStart !== null && rowEnd !== null) return `第 ${rowStart}-${rowEnd} 行`;
  return `第 ${rowStart ?? rowEnd} 行`;
}

function formatLocator(locator: Record<string, unknown>): string {
  const entries = Object.entries(locator);
  if (!entries.length) return "";
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
}
