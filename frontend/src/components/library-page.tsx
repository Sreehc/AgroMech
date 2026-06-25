"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import {
  ArrowsClockwise,
  CaretDown,
  CaretUp,
  Database,
  FileText,
  FunnelSimple,
  MagnifyingGlass,
  Trash,
  UploadSimple,
} from "@phosphor-icons/react";
import { FormEvent, useEffect, useState } from "react";
import Link from "next/link";

import {
  DocumentUploadQueue,
  isActiveUploadStatus,
  validateUploadFile,
  type UploadQueueItem,
} from "@/components/document-upload-queue";
import {
  collectFilterOptions,
  documentTypeOptions,
  languageOptions,
  mergeOptionValues,
  SearchableSelectField,
  SelectField,
  statusOptions,
} from "@/components/filter-controls";
import { Button } from "@/components/ui/button";
import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { EmptyState } from "@/components/ui/empty-state";
import { PageHeader } from "@/components/ui/page-header";
import { StatusBadge } from "@/components/ui/status-badge";
import {
  ApiRequestError,
  canMutateLibrary,
  deleteDocument,
  emptyDocumentFilters,
  errorMessage,
  listDocuments,
  reprocessDocument,
  uploadDocument,
  type DocumentFilters,
  type DocumentSummary,
} from "@/lib/frontend-api";
import { loadSession, type Session } from "@/lib/session";

export function LibraryPage() {
  const [session] = useState<Session | null>(() => {
    if (typeof window === "undefined") return null;
    return loadSession();
  });
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [expandedDocumentId, setExpandedDocumentId] = useState<string | null>(
    null,
  );
  const [filters, setFilters] = useState<DocumentFilters>(emptyDocumentFilters);
  const [draftFilters, setDraftFilters] =
    useState<DocumentFilters>(emptyDocumentFilters);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<LibraryErrorState | null>(null);
  const [uploadQueue, setUploadQueue] = useState<LibraryUploadQueueItem[]>([]);
  const [uploading, setUploading] = useState(false);
  const [closeUploadConfirmationOpen, setCloseUploadConfirmationOpen] =
    useState(false);
  const [confirmation, setConfirmation] = useState<{
    action: "reprocess" | "delete";
    document: DocumentSummary;
  } | null>(null);

  const canMutate = session ? canMutateLibrary(session.role) : false;
  const filterOptions = collectFilterOptions(documents);
  const selectedBrand = draftFilters.brand.trim();
  const modelOptions = selectedBrand
    ? (filterOptions.modelsByBrand[selectedBrand] ?? filterOptions.allModels)
    : filterOptions.allModels;
  const mergedDocumentTypeOptions = mergeOptionValues(
    documentTypeOptions,
    filterOptions.documentTypes,
    {
      manual: "说明手册",
      repair_manual: "维修手册",
      "fault-code": "故障码",
    },
  );
  const mergedLanguageOptions = mergeOptionValues(
    languageOptions,
    filterOptions.languages,
    {
      "zh-CN": "中文（简体）",
      "en-US": "英文（美国）",
    },
  );
  const hasDraftFilters = hasDocumentFilters(draftFilters);
  const processingCount = documents.filter((document) =>
    ["queued", "processing", "reprocessing"].includes(document.status),
  ).length;
  const failedCount = documents.filter(
    (document) => document.status === "failed",
  ).length;
  const readyUploadCount = uploadQueue.filter(
    (item) => item.status === "pending" || item.status === "failed",
  ).length;
  const latestQueueItems = uploadQueue.slice(0, 3);
  const latestQueueLabel =
    latestQueueItems.length === 0
      ? "当前没有待观察的上传项目。"
      : `最近 ${latestQueueItems.length} 个上传项目会固定显示在这里。`;

  async function load(nextSession = session, nextFilters = filters) {
    if (!nextSession) return;
    setLoading(true);
    setError(null);
    try {
      const response = await listDocuments(nextSession.token, nextFilters);
      setDocuments(response.items);
      setTotal(response.total);
    } catch (caught) {
      setError({
        title: "资料列表加载失败",
        message:
          caught instanceof ApiRequestError
            ? errorMessage(caught.response)
            : "资料列表加载失败。",
        retryable: true,
      });
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void load(session, filters);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [session]);

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters(draftFilters);
    setExpandedDocumentId(null);
    void load(session, draftFilters);
  }

  function clearFilters() {
    setFilters(emptyDocumentFilters);
    setDraftFilters(emptyDocumentFilters);
    setExpandedDocumentId(null);
    void load(session, emptyDocumentFilters);
  }

  function addUploadFiles(files: File[]) {
    if (files.length === 0) return;
    setMessage(null);
    setError(null);
    setCloseUploadConfirmationOpen(false);
    setUploadQueue((current) => [
      ...current,
      ...files.map(createUploadQueueItem),
    ]);
  }

  function updateUploadQueueItem(
    itemId: string,
    patch: Partial<UploadQueueItem>,
  ) {
    setUploadQueue((current) =>
      current.map((item) =>
        item.id === itemId ? { ...item, ...patch } : item,
      ),
    );
  }

  async function uploadQueueItem(
    item: LibraryUploadQueueItem,
    retrying = false,
  ): Promise<boolean> {
    if (!session) return false;

    updateUploadQueueItem(item.id, {
      status: retrying ? "retrying" : "validating",
      progress: 0,
      error: null,
      documentId: undefined,
      taskId: undefined,
      taskStatus: undefined,
    });

    const validationError = validateUploadFile(item.file);
    if (validationError) {
      updateUploadQueueItem(item.id, {
        status: "failed",
        progress: 100,
        error: validationError,
      });
      return false;
    }

    updateUploadQueueItem(item.id, { status: "uploading", progress: 0 });
    try {
      const result = await uploadDocument(session.token, item.file, {
        onProgress: (progress) => {
          updateUploadQueueItem(item.id, { status: "uploading", progress });
        },
      });
      updateUploadQueueItem(item.id, {
        status: "uploaded",
        progress: 100,
        documentId: result.document_id,
        taskId: result.task_id,
        taskStatus: result.status,
      });
      return true;
    } catch (caught) {
      updateUploadQueueItem(item.id, {
        status: "failed",
        progress: 100,
        error:
          caught instanceof ApiRequestError
            ? errorMessage(caught.response)
            : "上传失败。",
      });
      return false;
    }
  }

  async function startUploadQueue() {
    if (!session || uploading) return;
    const candidates = uploadQueue.filter(
      (item) => item.status === "pending" || item.status === "failed",
    );
    if (candidates.length === 0) return;

    setUploading(true);
    setError(null);
    setMessage(null);
    try {
      const results = await Promise.all(
        candidates.map((item) =>
          uploadQueueItem(item, item.status === "failed"),
        ),
      );
      const successCount = results.filter(Boolean).length;
      const failedCount = results.length - successCount;
      setMessage(`上传完成：${successCount} 成功，${failedCount} 失败`);
      if (successCount > 0) {
        await load(session, filters);
      }
    } finally {
      setUploading(false);
    }
  }

  async function retryUploadQueueItem(itemId: string) {
    if (!session || uploading) return;
    const item = uploadQueue.find((queueItem) => queueItem.id === itemId);
    if (!item) return;

    setUploading(true);
    setError(null);
    setMessage(null);
    try {
      const uploaded = await uploadQueueItem(item, true);
      setMessage(uploaded ? "重试上传成功" : "重试上传失败");
      if (uploaded) {
        await load(session, filters);
      }
    } finally {
      setUploading(false);
    }
  }

  function removeUploadQueueItem(itemId: string) {
    setUploadQueue((current) => current.filter((item) => item.id !== itemId));
  }

  function requestCloseUploadQueue() {
    if (uploadQueue.some((item) => isActiveUploadStatus(item.status))) {
      setCloseUploadConfirmationOpen(true);
      return;
    }
    setUploadQueue([]);
    setCloseUploadConfirmationOpen(false);
  }

  function confirmCloseUploadQueue() {
    setUploadQueue([]);
    setCloseUploadConfirmationOpen(false);
  }

  async function confirmAction() {
    if (!session || !confirmation) return;
    setError(null);
    setMessage(null);
    try {
      if (confirmation.action === "reprocess") {
        const result = await reprocessDocument(
          session.token,
          confirmation.document.id,
        );
        setMessage(
          `document_id: ${result.document_id}\ntask_id: ${result.task_id}`,
        );
      } else {
        const result = await deleteDocument(
          session.token,
          confirmation.document.id,
        );
        setMessage(
          `document_id: ${result.document_id}\nstatus: ${result.status}`,
        );
      }
      setConfirmation(null);
      await load(session, filters);
    } catch (caught) {
      setError({
        title:
          confirmation.action === "reprocess" ? "重新处理失败" : "删除失败",
        message:
          caught instanceof ApiRequestError
            ? errorMessage(caught.response)
            : "操作失败。",
      });
    }
  }

  const hasActiveFilters = hasDocumentFilters(filters);

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto p-4 md:p-6">
      <PageHeader
        eyebrow="Library"
        title="资料库"
        description="查找资料、查看处理状态，或上传新资料。"
        actions={
          canMutate ? (
            <a
              className="inline-flex h-8 items-center justify-center gap-1.5 rounded-lg border border-border px-2.5 text-sm font-medium text-foreground"
              href="#library-upload"
            >
              <UploadSimple className="size-4" />
              上传资料
            </a>
          ) : null
        }
      />

      <div className="grid gap-5 lg:grid-cols-[minmax(320px,380px)_minmax(0,1fr)] lg:items-start">
        <aside className="grid gap-5 lg:sticky lg:top-6">
          <section className="grid gap-3 rounded-2xl border border-border bg-surface-panel/65 p-4">
            <div className="flex items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  上传与处理
                </p>
                <p className="mt-1 text-sm text-text-muted">
                  先添加文件，再点击“开始上传”。
                </p>
              </div>
              <div className="rounded-full border border-primary/20 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary">
                {readyUploadCount > 0
                  ? `待上传 ${readyUploadCount}`
                  : "队列已同步"}
              </div>
            </div>
            {canMutate ? (
              <DocumentUploadQueue
                busy={uploading}
                closeConfirmationOpen={closeUploadConfirmationOpen}
                items={uploadQueue}
                onCancelClose={() => setCloseUploadConfirmationOpen(false)}
                onConfirmClose={confirmCloseUploadQueue}
                onFilesSelected={addUploadFiles}
                onRemove={removeUploadQueueItem}
                onRequestClose={requestCloseUploadQueue}
                onRetry={retryUploadQueueItem}
                onStartUpload={startUploadQueue}
              />
            ) : (
              <div className="rounded-2xl border border-border/70 bg-surface-raised/85 p-4 text-sm text-text-muted">
                当前账号没有上传权限。
              </div>
            )}
          </section>

          <section className="grid gap-3 rounded-2xl border border-border bg-surface-panel/65 p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  最近动态
                </p>
                <p className="mt-1 text-sm text-text-muted">
                  {latestQueueLabel}
                </p>
              </div>
              <div className="flex gap-2">
                <MiniStat label="处理中" value={processingCount} tone="info" />
                <MiniStat label="失败" value={failedCount} tone="danger" />
              </div>
            </div>
            <div className="grid gap-2">
              <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-muted">
                上传队列
              </p>
              {latestQueueItems.length === 0 ? (
                <div className="rounded-xl border border-dashed border-border/80 bg-surface-raised/85 px-3 py-4 text-sm text-text-muted">
                  还没有加入上传队列的文件。可从上方拖拽或点击选择资料。
                </div>
              ) : (
                latestQueueItems.map((item) => (
                  <div
                    key={item.id}
                    className="rounded-xl border border-border/70 bg-surface-raised/85 px-3 py-3"
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <p className="truncate text-sm font-medium text-foreground">
                          {item.name}
                        </p>
                        <p className="mt-1 text-xs text-text-muted">
                          {Math.max(0, item.progress)}% ·{" "}
                          {item.status === "uploaded"
                            ? "已上传，后台处理中"
                            : item.status === "failed"
                              ? "需重试"
                              : "等待队列处理"}
                        </p>
                      </div>
                      <span className="rounded-full border border-border/60 bg-muted px-2 py-0.5 text-[11px] text-text-muted">
                        {item.status === "uploaded"
                          ? "后台处理中"
                          : item.status === "failed"
                            ? "失败"
                            : "队列中"}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </section>

          {message ? (
            <pre className="rounded-lg border border-status-success/30 bg-status-success/10 p-3 text-sm text-status-success">
              {message}
            </pre>
          ) : null}
          {error ? (
            <LibraryErrorAlert
              title={error.title}
              message={error.message}
              onRetry={
                error.retryable ? () => void load(session, filters) : undefined
              }
            />
          ) : null}
        </aside>

        <section className="grid gap-4">
          <div className="grid gap-4 rounded-2xl border border-border bg-surface-panel/65 p-4">
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <p className="text-sm font-semibold text-foreground">
                  资料浏览
                </p>
                <p className="mt-1 text-sm text-text-muted">
                  在这里查看资料状态、筛选结果和处理情况。
                </p>
              </div>
              <div className="text-xs text-text-muted">
                {loading ? "正在同步资料列表" : `当前资料状态 · ${total} 份`}
              </div>
            </div>
            <LibraryStatusOverview documents={documents} total={total} />
          </div>

          <form
            className="grid gap-3 rounded-2xl border border-border bg-surface-panel/65 p-4"
            onSubmit={submitFilters}
          >
            <div className="grid gap-3 xl:grid-cols-[1.2fr_1.2fr_1fr_1fr_1fr_auto]">
              <SearchableSelectField
                label="品牌"
                value={draftFilters.brand}
                options={filterOptions.brands}
                placeholder="选择品牌或直接输入"
                onChange={(value) =>
                  setDraftFilters({ ...draftFilters, brand: value })
                }
              />
              <SearchableSelectField
                label="型号"
                value={draftFilters.model}
                options={modelOptions}
                placeholder="选择型号或直接输入"
                onChange={(value) =>
                  setDraftFilters({ ...draftFilters, model: value })
                }
              />
              <SelectField
                label="类型"
                value={draftFilters.document_type}
                options={mergedDocumentTypeOptions}
                onChange={(value) =>
                  setDraftFilters({ ...draftFilters, document_type: value })
                }
              />
              <SelectField
                label="语言"
                value={draftFilters.language}
                options={mergedLanguageOptions}
                onChange={(value) =>
                  setDraftFilters({ ...draftFilters, language: value })
                }
              />
              <SelectField
                label="状态"
                value={draftFilters.status}
                options={statusOptions}
                onChange={(value) =>
                  setDraftFilters({ ...draftFilters, status: value })
                }
              />
              <button
                className="inline-flex items-center justify-center gap-2 self-end rounded-md bg-primary px-4 py-2 text-primary-foreground"
                type="submit"
              >
                <FunnelSimple className="size-4" />
                筛选
              </button>
            </div>
            <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-text-muted">
              <p>
                可直接输入品牌或型号；没有匹配项时按回车即可。
              </p>
              <div className="flex flex-wrap gap-2">
                {hasDraftFilters ? (
                  <button
                    className="font-medium text-foreground underline-offset-4 hover:underline"
                    type="button"
                    onClick={clearFilters}
                  >
                    重置
                  </button>
                ) : (
                  <span>未启用筛选条件</span>
                )}
              </div>
            </div>
          </form>

          <LibraryDocumentList
            canMutate={canMutate}
            documents={documents}
            expandedDocumentId={expandedDocumentId}
            hasActiveFilters={hasActiveFilters}
            loading={loading}
            total={total}
            onClearFilters={clearFilters}
            onDelete={(document) =>
              setConfirmation({ action: "delete", document })
            }
            onExpand={(documentId) =>
              setExpandedDocumentId(
                expandedDocumentId === documentId ? null : documentId,
              )
            }
            onReprocess={(document) =>
              setConfirmation({ action: "reprocess", document })
            }
          />
        </section>
      </div>

      <LibraryActionConfirmDialog
        confirmation={confirmation}
        onCancel={() => setConfirmation(null)}
        onConfirm={confirmAction}
      />
    </div>
  );
}

export function LibraryStatusOverview({
  documents,
  total,
}: {
  documents: DocumentSummary[];
  total: number;
}) {
  const processing = documents.filter((document) =>
    ["queued", "processing", "reprocessing"].includes(document.status),
  ).length;
  const indexed = documents.filter(
    (document) => document.status === "indexed",
  ).length;
  const failed = documents.filter(
    (document) => document.status === "failed",
  ).length;

  return (
    <section className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-muted">
          当前资料状态
        </p>
        <p className="text-xs text-text-muted">
          这里显示当前筛选范围内的资料状态。
        </p>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 xl:grid-cols-4">
        <OverviewItem label="总资料数" value={total} />
        <OverviewItem label="处理中" value={processing} />
        <OverviewItem label="已索引" value={indexed} />
        <OverviewItem label="失败" value={failed} tone="danger" />
      </div>
    </section>
  );
}

function OverviewItem({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "danger";
}) {
  return (
    <div className="rounded-2xl border border-border/70 bg-surface-raised/85 p-4">
      <p className="text-xs font-semibold uppercase tracking-[0.14em] text-text-muted">
        {label}
      </p>
      <p
        className={[
          "mt-2 text-3xl font-semibold leading-none",
          tone === "danger" ? "text-status-danger" : "text-foreground",
        ].join(" ")}
      >
        {value}
      </p>
    </div>
  );
}

function MiniStat({
  label,
  value,
  tone = "neutral",
}: {
  label: string;
  value: number;
  tone?: "neutral" | "info" | "danger";
}) {
  const toneClass =
    tone === "danger"
      ? "text-status-danger border border-status-danger/20 bg-status-danger/10"
      : tone === "info"
        ? "text-primary border border-primary/20 bg-primary/10"
        : "text-foreground border border-border/70 bg-surface-raised/85";

  return (
    <div className={["rounded-xl px-3 py-2 text-right", toneClass].join(" ")}>
      <p className="text-[11px] uppercase tracking-[0.12em]">{label}</p>
      <p className="mt-1 text-lg font-semibold leading-none">{value}</p>
    </div>
  );
}

export function LibraryDocumentList({
  canMutate,
  documents,
  expandedDocumentId,
  hasActiveFilters = false,
  loading,
  total,
  onClearFilters,
  onDelete,
  onExpand,
  onReprocess,
}: {
  canMutate: boolean;
  documents: DocumentSummary[];
  expandedDocumentId: string | null;
  hasActiveFilters?: boolean;
  loading: boolean;
  total: number;
  onClearFilters?: () => void;
  onDelete: (document: DocumentSummary) => void;
  onExpand: (documentId: string) => void;
  onReprocess: (document: DocumentSummary) => void;
}) {
  return (
    <section className="overflow-hidden rounded-2xl border border-border bg-surface-panel/65">
      <div className="flex items-center justify-between border-b border-border/80 px-4 py-3 text-sm text-text-muted">
        <span>共 {total} 份资料</span>
        {loading ? <span>加载中</span> : null}
      </div>
      <div className="divide-y divide-border/80">
        {documents.length === 0 && !loading ? (
          <EmptyState
            className="m-4"
            icon={
              hasActiveFilters ? (
                <MagnifyingGlass className="size-5" />
              ) : (
                <Database className="size-5" />
              )
            }
            title={hasActiveFilters ? "筛选无结果" : "暂无资料"}
            description={
              hasActiveFilters
                ? "当前筛选条件没有匹配资料，可以清空筛选后重新查看。"
                : "上传资料后，处理状态会显示在这里。"
            }
            action={
              hasActiveFilters ? (
                <Button
                  type="button"
                  variant="outline"
                  onClick={onClearFilters}
                >
                  清空筛选
                </Button>
              ) : null
            }
          />
        ) : null}
        {documents.map((document) => (
          <DocumentRow
            canMutate={canMutate}
            document={document}
            expanded={document.id === expandedDocumentId}
            key={document.id}
            onDelete={() => onDelete(document)}
            onExpand={() => onExpand(document.id)}
            onReprocess={() => onReprocess(document)}
          />
        ))}
      </div>
    </section>
  );
}

function DocumentRow({
  canMutate,
  document,
  expanded,
  onDelete,
  onExpand,
  onReprocess,
}: {
  canMutate: boolean;
  document: DocumentSummary;
  expanded: boolean;
  onDelete: () => void;
  onExpand: () => void;
  onReprocess: () => void;
}) {
  return (
    <article className="grid gap-0">
      <div className="grid gap-3 p-4 xl:grid-cols-[1.5fr_1fr_auto]">
        <div className="min-w-0">
          <h3 className="flex items-center gap-2 font-medium text-foreground">
            <FileText className="size-4 text-text-muted" />
            <span className="truncate">{document.title}</span>
          </h3>
          <p className="mt-1 truncate text-sm text-text-muted">
            {document.original_file_name}
          </p>
        </div>
        <div className="grid gap-1 text-sm text-text-muted md:grid-cols-3 xl:grid-cols-1">
          <p>
            {[document.brand, document.model].filter(Boolean).join(" / ") ||
              "未标注品牌型号"}
          </p>
          <p>
            {[document.document_type, document.language]
              .filter(Boolean)
              .join(" / ") || "未标注类型语言"}
          </p>
          <p>{document.updated_at ?? "未更新"}</p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={document.status} />
          <Button size="sm" variant="outline" type="button" onClick={onExpand}>
            {expanded ? (
              <CaretUp className="size-4" />
            ) : (
              <CaretDown className="size-4" />
            )}
            {expanded ? "收起" : "展开"}
          </Button>
          <Link
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2.5 text-[0.8rem]"
            href={`/library/${document.id}`}
          >
            查看详情
          </Link>
          {canMutate ? (
            <>
              <Button
                size="sm"
                variant="outline"
                type="button"
                onClick={onReprocess}
              >
                <ArrowsClockwise className="size-4" />
                重新处理
              </Button>
              <Button
                size="sm"
                variant="destructive"
                type="button"
                onClick={onDelete}
              >
                <Trash className="size-4" />
                删除
              </Button>
            </>
          ) : null}
        </div>
      </div>
      {expanded ? (
        <ExpandedDocumentRow
          document={document}
          canMutate={canMutate}
          onDelete={onDelete}
          onReprocess={onReprocess}
        />
      ) : null}
    </article>
  );
}

function ExpandedDocumentRow({
  document,
  canMutate,
  onDelete,
  onReprocess,
}: {
  document: DocumentSummary;
  canMutate: boolean;
  onDelete: () => void;
  onReprocess: () => void;
}) {
  const failure = document.failure;
  const recentTask = document.recent_task;

  return (
    <div className="grid gap-3 border-t border-border/80 bg-surface-panel/40 p-4 text-sm md:grid-cols-3">
      <div className="rounded-2xl border border-border/70 bg-surface-raised/85 p-3">
        <p className="font-medium text-foreground">列表内摘要</p>
        <p className="mt-2 leading-6 text-text-muted">
          {document.summary || document.original_file_name || "未返回摘要。"}
        </p>
      </div>
      <div className="rounded-2xl border border-border/70 bg-surface-raised/85 p-3">
        <p className="font-medium text-foreground">最近任务</p>
        <p className="mt-2 text-text-muted">
          {recentTask
            ? `${recentTask.id} · ${recentTask.task_type} · ${recentTask.status}`
            : "未返回最近任务。"}
        </p>
        {recentTask?.stage ? (
          <p className="mt-1 text-text-muted">阶段：{recentTask.stage}</p>
        ) : null}
      </div>
      <div className="rounded-2xl border border-border/70 bg-surface-raised/85 p-3">
        <p className="font-medium text-foreground">错误和快速操作</p>
        <p
          className={[
            "mt-2 leading-6",
            failure?.message ? "text-status-danger" : "text-text-muted",
          ].join(" ")}
        >
          {failure?.message
            ? `${failure.code ?? "unknown"} · ${failure.message}`
            : "未返回错误。"}
        </p>
        {failure?.stage ? (
          <p className="mt-1 text-status-danger">阶段：{failure.stage}</p>
        ) : null}
        <div className="mt-3 flex flex-wrap gap-2">
          <Link
            className="inline-flex h-7 items-center gap-1.5 rounded-md border border-border px-2.5 text-[0.8rem]"
            href={`/library/${document.id}`}
          >
            查看详情
          </Link>
          {canMutate ? (
            <>
              <Button
                size="sm"
                variant="outline"
                type="button"
                onClick={onReprocess}
              >
                重新处理
              </Button>
              <Button
                size="sm"
                variant="destructive"
                type="button"
                onClick={onDelete}
              >
                删除
              </Button>
            </>
          ) : null}
        </div>
      </div>
    </div>
  );
}

function hasDocumentFilters(filters: DocumentFilters): boolean {
  return Object.values(filters).some((value) => value.trim().length > 0);
}

type LibraryErrorState = {
  title: string;
  message: string;
  retryable?: boolean;
};

export function LibraryErrorAlert({
  title = "资料列表加载失败",
  message,
  onRetry,
}: {
  title?: string;
  message: string;
  onRetry?: () => void;
}) {
  return (
    <Alert tone="danger">
      <AlertTitle>{title}</AlertTitle>
      <AlertDescription>{message}</AlertDescription>
      {onRetry ? (
        <div className="mt-2">
          <Button type="button" variant="outline" size="sm" onClick={onRetry}>
            重试加载
          </Button>
        </div>
      ) : null}
    </Alert>
  );
}

export function LibraryActionConfirmDialog({
  confirmation,
  onCancel,
  onConfirm,
}: {
  confirmation: {
    action: "reprocess" | "delete";
    document: DocumentSummary;
  } | null;
  onCancel: () => void;
  onConfirm: () => void;
}) {
  const content = libraryActionConfirmationContent(confirmation);

  return (
    <ConfirmDialog
      open={Boolean(confirmation)}
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

export function libraryActionConfirmationContent(
  confirmation: {
    action: "reprocess" | "delete";
    document: DocumentSummary;
  } | null,
): {
  title: string;
  description: string;
  confirmLabel: string;
  destructive: boolean;
} {
  if (confirmation?.action === "delete") {
    return {
      title: "确认删除资料",
      description: `${confirmation.document.title}。删除后历史引用只能保留元数据，资料详情和原文预览将不可访问。`,
      confirmLabel: "确认删除",
      destructive: true,
    };
  }
  return {
    title: "确认重新处理",
    description: `${confirmation?.document.title ?? ""}。会创建新的资料处理任务，处理期间资料状态会更新。`,
    confirmLabel: "确认重新处理",
    destructive: false,
  };
}

type LibraryUploadQueueItem = UploadQueueItem & {
  file: File;
};

function createUploadQueueItem(
  file: File,
  index: number,
): LibraryUploadQueueItem {
  return {
    id: `${file.name}-${file.size}-${file.lastModified}-${Date.now()}-${index}`,
    file,
    name: file.name,
    size: file.size,
    type: file.type,
    status: "pending",
    progress: 0,
  };
}
