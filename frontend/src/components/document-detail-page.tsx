"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import { ArrowLeft, FileMagnifyingGlass, Trash, Wrench } from "@phosphor-icons/react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { DocumentPreviewPanel } from "@/components/document-preview";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiRequestError,
  canMutateLibrary,
  deleteDocument,
  errorMessage,
  getDocument,
  getDocumentPreview,
  reprocessDocument,
  type DocumentDetail,
  type DocumentFailure,
  type DocumentPreviewResponse,
  type DocumentTaskSummary,
} from "@/lib/frontend-api";
import { loadSession, type Session } from "@/lib/session";

export function DocumentDetailPage({ documentId }: { documentId: string }) {
  const [session] = useState<Session | null>(() => {
    if (typeof window === "undefined") return null;
    return loadSession();
  });
  const [document, setDocument] = useState<DocumentDetail | null>(null);
  const [preview, setPreview] = useState<DocumentPreviewResponse | null>(null);
  const [previewLoading, setPreviewLoading] = useState(false);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<"reprocess" | "delete" | null>(null);

  async function loadDocument(nextSession = session) {
    if (!nextSession) {
      setError("登录状态不可用，请重新登录。");
      return;
    }
    setLoading(true);
    setError(null);
    setPreview(null);
    setPreviewError(null);
    try {
      const detail = await getDocument(nextSession.token, documentId);
      setDocument(detail);
      const firstChunk = detail.chunks[0];
      if (firstChunk) {
        setPreviewLoading(true);
        try {
          setPreview(await getDocumentPreview(nextSession.token, documentId, firstChunk.id));
        } catch (caught) {
          setPreviewError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "原文预览加载失败。");
        } finally {
          setPreviewLoading(false);
        }
      }
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "资料详情加载失败。");
    } finally {
      setLoading(false);
    }
  }

  async function confirmMutation() {
    if (!session || !document || !pendingAction) return;
    setError(null);
    setMessage(null);
    try {
      if (pendingAction === "reprocess") {
        const result = await reprocessDocument(session.token, document.id);
        setMessage(`document_id: ${result.document_id}\ntask_id: ${result.task_id}`);
      } else {
        const result = await deleteDocument(session.token, document.id);
        setMessage(`document_id: ${result.document_id}\nstatus: ${result.status}`);
      }
      setPendingAction(null);
      await loadDocument(session);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "操作失败。");
    }
  }

  useEffect(() => {
    void loadDocument(session);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session, documentId]);

  const canMutate = session ? canMutateLibrary(session.role) : false;

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto p-4 md:p-6">
      {loading && !document ? <p className="text-sm text-text-muted">正在加载资料详情...</p> : null}
      {error ? <DocumentDetailErrorState message={error} onRetry={() => void loadDocument(session)} /> : null}
      {message ? <pre className="rounded-lg border border-status-success/30 bg-status-success/10 p-3 text-sm text-status-success">{message}</pre> : null}
      {document ? (
        <DocumentDetailView
          document={document}
          canMutate={canMutate}
          onDelete={() => setPendingAction("delete")}
          onReprocess={() => setPendingAction("reprocess")}
          preview={preview}
          previewLoading={previewLoading}
          previewError={previewError}
        />
      ) : null}
      {!loading && !document && !error ? (
        <EmptyState
          icon={<FileMagnifyingGlass className="size-5" />}
          title="资料详情不可用"
          description="未能读取资料详情，请返回资料库重试。"
        />
      ) : null}
      <DocumentDetailActionConfirmDialog
        action={pendingAction}
        document={document}
        onCancel={() => setPendingAction(null)}
        onConfirm={confirmMutation}
      />
    </div>
  );
}

export function DocumentDetailView({
  document,
  canMutate,
  onReprocess,
  onDelete,
  preview,
  previewLoading = false,
  previewError = null,
}: {
  document: DocumentDetail;
  canMutate: boolean;
  onReprocess?: () => void;
  onDelete?: () => void;
  preview?: DocumentPreviewResponse | null;
  previewLoading?: boolean;
  previewError?: string | null;
}) {
  return (
    <>
      <PageHeader
        eyebrow="Document Detail"
        title={document.title}
        description="查看这份资料的状态、内容摘要和处理记录。"
        actions={
          <>
            <Link
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border bg-background px-2.5 text-sm font-medium text-foreground transition hover:bg-muted"
              href="/library"
            >
              <ArrowLeft className="size-4" />
              返回资料库
            </Link>
            <StatusBadge status={document.status} />
          </>
        }
      />

      <section className="grid gap-4 rounded-lg border border-border bg-surface-panel p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <h2 className="text-lg font-semibold text-foreground">{document.metadata.original_file_name}</h2>
            <p className="mt-1 text-sm text-text-muted">资料 ID：{document.id}</p>
          </div>
          <StatusBadge status={document.status} />
        </div>

        <dl className="grid gap-3 text-sm md:grid-cols-4">
          <MetadataItem label="品牌" value={document.metadata.brand} />
          <MetadataItem label="型号" value={document.metadata.model} />
          <MetadataItem label="类型" value={document.metadata.document_type} />
          <MetadataItem label="语言" value={document.metadata.language} />
          <MetadataItem label="来源" value={document.metadata.source} />
          <MetadataItem label="MIME" value={document.metadata.mime_type} />
          <MetadataItem label="文件大小" value={formatBytes(document.metadata.file_size_bytes)} />
          <MetadataItem label="更新时间" value={document.updated_at ?? null} />
        </dl>
      </section>

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_20rem]">
        <section className="grid gap-4">
          <TaskCard task={document.recent_task} />
          <FailureCard failure={document.failure} />
          <section className="grid gap-3 rounded-lg border border-border bg-surface-panel p-4">
            <h2 className="text-base font-semibold text-foreground">引用预览</h2>
            {document.chunks.length ? (
              <div className="grid gap-2">
                {document.chunks.map((chunk) => (
                  <article className="rounded-lg border border-border bg-surface-raised p-3" key={chunk.id}>
                    <div className="flex flex-wrap items-center justify-between gap-2">
                      <h3 className="font-medium text-foreground">{chunk.summary || "未返回摘要"}</h3>
                      <span className="text-xs text-text-muted">{chunk.chunk_type}</span>
                    </div>
                    <p className="mt-2 text-sm text-text-muted">
                      {[chunk.section_title, chunk.page_number ? `第 ${chunk.page_number} 页` : null].filter(Boolean).join(" · ") ||
                        "未返回来源定位"}
                    </p>
                  </article>
                ))}
              </div>
            ) : (
              <EmptyState
                className="py-8"
                icon={<FileMagnifyingGlass className="size-5" />}
                title="暂无引用预览"
                description="资料尚未产生可展示的 chunk 摘要。"
              />
            )}
          </section>
          <PreviewSection preview={preview ?? null} loading={previewLoading} error={previewError} hasChunks={document.chunks.length > 0} />
        </section>

        <aside className="h-fit rounded-lg border border-border bg-surface-panel p-4">
          <h2 className="text-base font-semibold text-foreground">操作区</h2>
          {canMutate ? (
            <div className="mt-3 grid gap-2">
              <Button type="button" variant="outline" onClick={onReprocess}>
                <Wrench className="size-4" />
                重新处理
              </Button>
              <Button type="button" variant="destructive" onClick={onDelete}>
                <Trash className="size-4" />
                删除资料
              </Button>
            </div>
          ) : (
            <p className="mt-3 rounded-lg border border-border bg-surface-raised p-3 text-sm text-text-muted">
              当前角色仅可查看资料详情。
            </p>
          )}
        </aside>
      </div>
    </>
  );
}

function PreviewSection({
  preview,
  loading,
  error,
  hasChunks,
}: {
  preview: DocumentPreviewResponse | null;
  loading: boolean;
  error: string | null;
  hasChunks: boolean;
}) {
  if (preview) {
    return <DocumentPreviewPanel preview={preview} />;
  }
  if (loading) {
    return <p className="rounded-lg border border-border bg-surface-panel p-4 text-sm text-text-muted">正在加载原文预览...</p>;
  }
  if (error) {
    return (
      <Alert tone="warning">
        <AlertTitle>原文预览加载失败</AlertTitle>
        <AlertDescription>{error}</AlertDescription>
      </Alert>
    );
  }
  return (
    <EmptyState
      className="py-8"
      icon={<FileMagnifyingGlass className="size-5" />}
      title={hasChunks ? "暂无原文预览" : "暂无引用预览"}
      description={hasChunks ? "当前资料有 chunk 摘要，但暂未返回原文预览数据。" : "资料尚未产生可展示的原文定位。"}
    />
  );
}

export function DocumentDetailErrorState({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <Alert tone="danger">
      <AlertTitle>资料不可访问</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
      <div className="mt-2">
        <Button type="button" variant="outline" size="sm" onClick={onRetry}>
          重试加载
        </Button>
      </div>
    </Alert>
  );
}

export function DocumentDetailActionConfirmDialog({
  action,
  document,
  onCancel,
  onConfirm,
}: {
  action: "reprocess" | "delete" | null;
  document: DocumentDetail | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const content = documentDetailActionConfirmationContent(action, document);

  return (
    <ConfirmDialog
      open={Boolean(action && document)}
      title={content.title}
      description={content.description}
      confirmLabel={content.confirmLabel}
      destructive={content.destructive}
      onOpenChange={(open) => {
        if (!open) onCancel();
      }}
      onConfirm={onConfirm}
    />
  );
}

export function documentDetailActionConfirmationContent(
  action: "reprocess" | "delete" | null,
  document: DocumentDetail | null,
): {
  title: string;
  description: string;
  confirmLabel: string;
  destructive: boolean;
} {
  const documentName = document?.metadata.original_file_name ?? document?.title ?? "";
  if (action === "delete") {
    return {
      title: "确认删除资料",
      description: `${documentName}。删除后历史引用只能保留元数据，资料详情和原文预览将不可访问。`,
      confirmLabel: "确认删除",
      destructive: true,
    };
  }
  return {
    title: "确认重新处理",
    description: `${documentName}。会创建新的资料处理任务，处理期间资料状态会更新。`,
    confirmLabel: "确认重新处理",
    destructive: false,
  };
}

function MetadataItem({ label, value }: { label: string; value: string | number | null | undefined }) {
  return (
    <div className="grid gap-1 rounded-lg border border-border bg-surface-raised p-3">
      <dt className="text-xs text-text-muted">{label}</dt>
      <dd className="break-words font-medium text-foreground">{value || "未标注"}</dd>
    </div>
  );
}

function TaskCard({ task }: { task: DocumentTaskSummary | null }) {
  return (
    <section className="grid gap-3 rounded-lg border border-border bg-surface-panel p-4">
      <h2 className="text-base font-semibold text-foreground">最近任务</h2>
      {task ? (
        <dl className="grid gap-3 text-sm md:grid-cols-3">
          <MetadataItem label="任务 ID" value={task.id} />
          <MetadataItem label="类型" value={task.task_type} />
          <MetadataItem label="状态" value={task.status} />
          <MetadataItem label="阶段" value={task.stage} />
          <MetadataItem label="尝试次数" value={task.attempt_count} />
          <MetadataItem label="完成时间" value={task.finished_at} />
        </dl>
      ) : (
        <p className="rounded-lg border border-border bg-surface-raised p-3 text-sm text-text-muted">暂无最近任务。</p>
      )}
    </section>
  );
}

function FailureCard({ failure }: { failure: DocumentFailure }) {
  const hasFailure = Boolean(failure.stage || failure.code || failure.message);

  return (
    <section className="grid gap-3 rounded-lg border border-border bg-surface-panel p-4">
      <h2 className="text-base font-semibold text-foreground">失败信息</h2>
      {hasFailure ? (
        <Alert tone="danger">
          <AlertTitle>{failure.code || "处理失败"}</AlertTitle>
          <AlertDescription>
            {[failure.stage ? `阶段：${failure.stage}` : null, failure.message].filter(Boolean).join("；")}
          </AlertDescription>
        </Alert>
      ) : (
        <p className="rounded-lg border border-status-success/30 bg-status-success/10 p-3 text-sm text-status-success">
          未返回失败信息。
        </p>
      )}
    </section>
  );
}

function formatBytes(value: number | null): string | null {
  if (value === null) return null;
  if (value < 1024) return `${value} B`;
  if (value < 1024 * 1024) return `${(value / 1024).toFixed(1)} KB`;
  return `${(value / (1024 * 1024)).toFixed(1)} MB`;
}
