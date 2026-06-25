"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import {
  Copy,
  FileMagnifyingGlass,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { DocumentPreviewPanel } from "@/components/document-preview";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import type {
  AgroMechCitation,
  AgroMechStructuredPayload,
} from "@/lib/agromech-chat";
import {
  ApiRequestError,
  errorMessage,
  getDocumentPreview,
  getRetrievalTrace,
  type DocumentPreviewResponse,
  type RetrievalTrace,
} from "@/lib/frontend-api";

export function EvidencePanel({
  payload,
  activeIndex = 0,
  preview,
  previewToken,
  onSelect,
  onClose,
}: {
  payload?: AgroMechStructuredPayload | null;
  activeIndex?: number;
  preview?: DocumentPreviewResponse | null;
  previewToken?: string;
  onSelect?: (index: number) => void;
  onClose?: () => void;
}) {
  const citations = payload?.citations ?? [];
  const safeIndex = citations.length
    ? Math.min(Math.max(activeIndex, 0), citations.length - 1)
    : 0;
  const citation = citations[safeIndex] ?? null;
  const [fetchedPreview, setFetchedPreview] =
    useState<DocumentPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [traceLoading, setTraceLoading] = useState(false);
  const [traceError, setTraceError] = useState<string | null>(null);

  useEffect(() => {
    if (preview !== undefined) return;
    if (!previewToken || !citation?.document_id || !citation.accessible) {
      setFetchedPreview(null);
      setPreviewError(null);
      setPreviewLoading(false);
      return;
    }

    let cancelled = false;
    setPreviewLoading(true);
    setPreviewError(null);
    void getDocumentPreview(
      previewToken,
      citation.document_id,
      citation.chunk_id ?? undefined,
    )
      .then((result) => {
        if (!cancelled) setFetchedPreview(result);
      })
      .catch((caught) => {
        if (!cancelled) {
          setFetchedPreview(null);
          setPreviewError(
            caught instanceof ApiRequestError
              ? errorMessage(caught.response)
              : "原文预览加载失败。",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setPreviewLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [
    citation?.accessible,
    citation?.chunk_id,
    citation?.document_id,
    preview,
    previewToken,
  ]);

  useEffect(() => {
    if (!previewToken || !payload?.trace_id) {
      setTrace(null);
      setTraceError(null);
      setTraceLoading(false);
      return;
    }

    let cancelled = false;
    setTraceLoading(true);
    setTraceError(null);
    void getRetrievalTrace(previewToken, payload.trace_id)
      .then((result) => {
        if (!cancelled) setTrace(result);
      })
      .catch((caught) => {
        if (!cancelled) {
          setTrace(null);
          setTraceError(
            caught instanceof ApiRequestError
              ? errorMessage(caught.response)
              : "Trace 加载失败。",
          );
        }
      })
      .finally(() => {
        if (!cancelled) setTraceLoading(false);
      });

    return () => {
      cancelled = true;
    };
  }, [payload?.trace_id, previewToken]);

  if (!payload || !citations.length || !citation) {
    return (
      <div className="grid gap-4" data-evidence-panel>
        <PanelHeader onClose={onClose} />
        <EmptyState
          className="py-10"
          icon={<FileMagnifyingGlass className="size-5" />}
          title={payload ? "未返回结构化证据" : "尚未选择引用"}
          description={
            payload
              ? "当前回答没有可打开的引用来源。"
              : "点击回答中的引用，这里会显示对应证据。"
          }
        />
      </div>
    );
  }

  const previewToShow = preview ?? fetchedPreview;

  return (
    <div className="grid gap-4" data-evidence-panel>
      <PanelHeader onClose={onClose} />

      <div className="grid gap-2">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-text-muted">
          引用列表
        </p>
        <div className="grid gap-1 rounded-2xl border border-border bg-surface-panel/65 p-3">
          {citations.map((item, index) => (
            <button
              className={[
                "rounded-xl border border-border/70 px-3 py-2 text-left text-sm transition",
                index === safeIndex
                  ? "border-primary/40 bg-primary/10 text-foreground"
                  : "bg-surface-raised/85 text-text-muted hover:bg-surface-inset/85 hover:text-foreground",
              ].join(" ")}
              key={`${item.document_id ?? "unknown"}-${item.chunk_id ?? index}`}
              type="button"
              onClick={() => onSelect?.(index)}
            >
              <span className="flex items-center justify-between gap-2">
                <span className="truncate font-medium">
                  {index + 1}. {item.document_title}
                </span>
                <Badge tone={item.accessible ? "success" : "danger"}>
                  {item.accessible ? "可访问" : "不可访问"}
                </Badge>
              </span>
              <span className="mt-1 block truncate text-xs opacity-75">
                {item.evidence_snippet}
              </span>
            </button>
          ))}
        </div>
      </div>

      <CitationDetail citation={citation} />
      <PreviewRegion
        citation={citation}
        error={previewError}
        loading={previewLoading}
        preview={previewToShow}
      />
      <TraceSummary
        trace={trace}
        traceError={traceError}
        traceId={payload.trace_id}
        traceLoading={traceLoading}
      />
      <UncertaintySummary payload={payload} />
    </div>
  );
}

function PanelHeader({ onClose }: { onClose?: () => void }) {
  return (
    <div className="flex items-start justify-between gap-3">
      <div>
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-text-muted">
          Evidence
        </p>
        <h2 className="text-base font-semibold text-foreground">证据面板</h2>
        <p className="mt-2 text-sm leading-6 text-text-muted">
          查看引用内容、来源位置和资料详情。
        </p>
      </div>
      {onClose ? (
        <Button
          aria-label="关闭证据面板"
          size="icon-sm"
          variant="ghost"
          type="button"
          onClick={onClose}
        >
          <X className="size-4" />
        </Button>
      ) : null}
    </div>
  );
}

function CitationDetail({ citation }: { citation: AgroMechCitation }) {
  const detailHref = citation.document_id
    ? `/library/${citation.document_id}`
    : undefined;

  return (
    <section className="grid gap-3 rounded-2xl border border-border bg-surface-panel/65 p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <h3 className="truncate font-medium text-foreground">
            {citation.document_title}
          </h3>
          <p className="mt-1 text-xs text-text-muted">
            {formatLocator(citation.source_locator)}
          </p>
        </div>
        <Badge tone={citation.accessible ? "success" : "danger"}>
          {citation.accessible ? "来源可访问" : "来源不可访问"}
        </Badge>
      </div>

      <div className="rounded-xl border border-border/70 bg-surface-raised/85 p-3">
        <p className="text-xs font-semibold uppercase tracking-[0.12em] text-text-muted">
          证据片段
        </p>
        <p className="mt-2 text-sm leading-6 text-foreground">
          {citation.evidence_snippet || "未返回证据片段。"}
        </p>
      </div>

      {citation.accessible && detailHref ? (
        <Link
          className="inline-flex h-7 w-fit items-center justify-center rounded-xl border border-border/70 bg-surface-raised/85 px-2.5 text-[0.8rem] font-medium text-foreground transition hover:bg-surface-inset/85"
          href={detailHref}
        >
          查看资料详情
        </Link>
      ) : (
        <p className="flex items-start gap-2 rounded-lg border border-status-danger/30 bg-status-danger/10 px-3 py-2 text-sm text-status-danger">
          <WarningCircle className="mt-0.5 size-4" />
          来源不可访问，仍保留文档名、定位和证据片段。
        </p>
      )}
    </section>
  );
}

function PreviewRegion({
  citation,
  preview,
  loading,
  error,
}: {
  citation: AgroMechCitation;
  preview: DocumentPreviewResponse | null;
  loading: boolean;
  error: string | null;
}) {
  if (preview) {
    return <DocumentPreviewPanel preview={preview} />;
  }
  if (loading) {
    return (
      <p className="rounded-2xl border border-border bg-surface-panel/65 p-3 text-sm text-text-muted">
        正在加载原文预览...
      </p>
    );
  }
  if (error) {
    return (
      <Alert tone="warning">
        <AlertTitle>原文预览加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  if (!citation.accessible) return null;
  return (
    <EmptyState
      className="py-8"
      icon={<FileMagnifyingGlass className="size-5" />}
      title="原文预览暂未加载"
      description="这里会显示对应的原文内容。"
    />
  );
}

function TraceSummary({
  traceId,
  trace,
  traceLoading,
  traceError,
}: {
  traceId: string | null;
  trace: RetrievalTrace | null;
  traceLoading: boolean;
  traceError: string | null;
}) {
  async function copyTraceId() {
    if (!traceId || typeof navigator === "undefined") return;
    await navigator.clipboard?.writeText(traceId);
  }

  return (
    <section className="grid gap-2 rounded-2xl border border-border bg-surface-panel/65 p-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h3 className="font-medium text-foreground">Trace ID</h3>
        {traceId ? (
          <Button
            size="sm"
            variant="outline"
            type="button"
            onClick={copyTraceId}
          >
            <Copy className="size-3.5" />
            复制
          </Button>
        ) : null}
      </div>
      <p className="break-all text-sm text-text-muted">
        {traceId || "未返回 trace"}
      </p>

      <div className="rounded-xl border border-border/70 bg-surface-raised/85 p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h4 className="font-medium text-foreground">Trace 摘要</h4>
          {trace?.rerank?.strategy ? (
            <Badge tone="neutral">{trace.rerank.strategy}</Badge>
          ) : null}
        </div>

        {traceLoading ? (
          <p className="mt-2 text-sm text-text-muted">正在加载 trace 摘要...</p>
        ) : traceError ? (
          <p className="mt-2 text-sm text-status-warning">{traceError}</p>
        ) : trace ? (
          <div className="mt-3 grid gap-3 text-sm text-text-muted">
            <p>可查看本次检索使用的证据。</p>
            <div className="flex flex-wrap gap-2">
              {trace.channels.used.map((channel) => (
                <Badge key={channel} tone="info">
                  {channel}
                </Badge>
              ))}
            </div>
            {trace.channels.degraded.length ? (
              <div className="grid gap-1">
                <p className="font-medium text-foreground">降级通道</p>
                {trace.channels.degraded.map((item) => (
                  <p key={`${item.channel}-${item.reason}`}>
                    {item.channel}: {item.reason}
                  </p>
                ))}
              </div>
            ) : null}
            {trace.final_evidence.length ? (
              <div className="grid gap-1">
                <p className="font-medium text-foreground">最终证据</p>
                <p>
                  {trace.final_evidence
                    .map((item) => String(item.chunk_id ?? "unknown"))
                    .join("、")}
                </p>
              </div>
            ) : null}
            {trace.rerank?.items?.length ? (
              <div className="grid gap-1">
                <p className="font-medium text-foreground">Rerank</p>
                <p>
                  Top {trace.rerank.items.length}，首条{" "}
                  {trace.rerank.items[0]?.chunk_id ?? "unknown"} 从
                  {` ${trace.rerank.items[0]?.before_rank ?? "-"} -> ${trace.rerank.items[0]?.after_rank ?? "-"}`}
                </p>
              </div>
            ) : null}
          </div>
        ) : (
          <p className="mt-2 text-sm text-text-muted">
            可查看本次检索使用的证据。
          </p>
        )}
      </div>
    </section>
  );
}

function UncertaintySummary({
  payload,
}: {
  payload: AgroMechStructuredPayload;
}) {
  return (
    <section className="grid gap-3 rounded-2xl border border-border bg-surface-panel/65 p-3">
      <div className="grid gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="font-medium text-foreground">不确定性</h3>
          <Badge tone="warning">{payload.uncertainty.level}</Badge>
        </div>
        <p className="text-sm leading-6 text-text-muted">
          {payload.uncertainty.reasons.length
            ? payload.uncertainty.reasons.join("、")
            : "未返回不确定性原因。"}
        </p>
      </div>

      {payload.safety_warnings.length ? (
        <div className="rounded-lg border border-status-warning/30 bg-status-warning/10 p-3 text-sm text-status-warning">
          <p className="font-medium">安全提醒</p>
          <p className="mt-1 leading-6">{payload.safety_warnings.join("；")}</p>
        </div>
      ) : null}
    </section>
  );
}

function formatLocator(locator: Record<string, unknown>): string {
  const entries = Object.entries(locator);
  if (!entries.length) return "来源定位不可用";
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
}
