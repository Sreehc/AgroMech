"use client";

import {
  ArrowCounterClockwise,
  CheckCircle,
  CloudArrowUp,
  FileArrowUp,
  Trash,
  WarningCircle,
  X,
} from "@phosphor-icons/react";
import type { DragEvent } from "react";

import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";

export type UploadQueueStatus =
  | "pending"
  | "validating"
  | "uploading"
  | "uploaded"
  | "failed"
  | "retrying"
  | "removed";

export type UploadQueueItem = {
  id: string;
  name: string;
  size: number;
  type: string;
  status: UploadQueueStatus;
  progress: number;
  error?: string | null;
  documentId?: string;
  taskId?: string;
  taskStatus?: string;
};

const uploadStatusLabels: Record<UploadQueueStatus, string> = {
  pending: "待开始上传",
  validating: "校验中",
  uploading: "上传中",
  uploaded: "已上传",
  failed: "上传失败",
  retrying: "重试中",
  removed: "已移除",
};

const supportedUploadExtensions = new Set([
  "pdf",
  "docx",
  "xlsx",
  "csv",
  "png",
  "jpg",
  "jpeg",
  "webp",
  "md",
  "markdown",
  "txt",
]);

const activeUploadStatuses: UploadQueueStatus[] = [
  "validating",
  "uploading",
  "retrying",
];

export function uploadQueueStatusLabel(status: UploadQueueStatus): string {
  return uploadStatusLabels[status];
}

export function isActiveUploadStatus(status: UploadQueueStatus): boolean {
  return activeUploadStatuses.includes(status);
}

export function validateUploadFile(
  file: Pick<File, "name" | "type">,
): string | null {
  const extension = file.name.split(".").pop()?.toLowerCase() ?? "";
  if (!supportedUploadExtensions.has(extension)) {
    return "不支持的文件类型";
  }
  return null;
}

export function DocumentUploadQueue({
  items,
  busy = false,
  closeConfirmationOpen = false,
  onCancelClose,
  onConfirmClose,
  onFilesSelected,
  onRemove,
  onRequestClose,
  onRetry,
  onStartUpload,
}: {
  items: UploadQueueItem[];
  busy?: boolean;
  closeConfirmationOpen?: boolean;
  onCancelClose?: () => void;
  onConfirmClose?: () => void;
  onFilesSelected: (files: File[]) => void;
  onRemove: (itemId: string) => void;
  onRequestClose: () => void;
  onRetry: (itemId: string) => void;
  onStartUpload: () => void;
}) {
  const readyCount = items.filter(
    (item) => item.status === "pending" || item.status === "failed",
  ).length;
  const hasActiveUpload = items.some((item) =>
    isActiveUploadStatus(item.status),
  );

  function handleDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    onFilesSelected(Array.from(event.dataTransfer.files));
  }

  return (
    <section id="library-upload" className="grid gap-3">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <div className="flex gap-2">
          {items.length > 0 ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onRequestClose}
            >
              <X className="size-4" />
              关闭队列
            </Button>
          ) : null}
          <Button
            type="button"
            size="sm"
            onClick={onStartUpload}
            disabled={readyCount === 0 || busy}
          >
            <FileArrowUp className="size-4" />
            {busy ? "上传中" : `开始上传（${readyCount}）`}
          </Button>
        </div>
      </div>

      <UploadSteps items={items} />

      {readyCount > 0 ? (
        <div className="rounded-xl border border-primary/25 bg-primary/10 px-3 py-2 text-sm text-primary">
          已加入队列，点击“开始上传”后才会真正上传。
        </div>
      ) : null}

      <label
        className="grid cursor-pointer place-items-center rounded-lg border border-dashed border-border bg-surface-canvas px-4 py-6 text-center transition-colors hover:bg-muted/60"
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <CloudArrowUp className="size-7 text-primary" />
        <span className="mt-2 text-sm font-medium text-foreground">
          拖拽多份资料到这里
        </span>
        <span className="mt-1 text-xs text-text-muted">
          支持 PDF、DOCX、XLSX、CSV、图片、Markdown 和 TXT
        </span>
        <input
          className="sr-only"
          type="file"
          multiple
          accept=".pdf,.docx,.xlsx,.csv,.png,.jpg,.jpeg,.webp,.md,.markdown,.txt"
          onChange={(event) => {
            onFilesSelected(Array.from(event.target.files ?? []));
            event.currentTarget.value = "";
          }}
        />
      </label>

      {items.length > 0 ? (
        <div className="grid gap-0 rounded-2xl border border-border/70 bg-surface-raised/80">
          {items.map((item) => (
            <UploadQueueRow
              busy={busy}
              item={item}
              key={item.id}
              onRemove={() => onRemove(item.id)}
              onRetry={() => onRetry(item.id)}
            />
          ))}
        </div>
      ) : null}

      {closeConfirmationOpen ? (
        <div className="rounded-xl border border-status-warning/40 bg-status-warning/10 p-3">
          <p className="flex items-center gap-2 text-sm font-medium text-status-warning">
            <WarningCircle className="size-4" />
            上传中关闭队列需要确认
          </p>
          <p className="mt-1 text-sm text-text-muted">
            {hasActiveUpload
              ? "仍有文件正在上传，关闭队列后页面不再展示这些文件的进度。"
              : "关闭后将清空当前上传队列。"}
          </p>
          <div className="mt-3 flex justify-end gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onCancelClose}
            >
              继续上传
            </Button>
            <Button
              type="button"
              variant="destructive"
              size="sm"
              onClick={onConfirmClose}
            >
              确认关闭
            </Button>
          </div>
        </div>
      ) : null}
    </section>
  );
}

// 顶部三步指示器：借鉴 rag-web-ui 的圆形步骤条，但当前步由队列状态推导，
// 而非独立向导状态，避免和真实上传状态脱节。
function UploadSteps({ items }: { items: UploadQueueItem[] }) {
  const currentStep = resolveUploadStep(items);
  const steps: Array<{ step: number; label: string; icon: typeof CloudArrowUp }> = [
    { step: 1, label: "添加文件", icon: FileArrowUp },
    { step: 2, label: "上传", icon: CloudArrowUp },
    { step: 3, label: "后台处理", icon: CheckCircle },
  ];

  return (
    <ol className="flex items-center gap-2" aria-label="上传流程">
      {steps.map(({ step, label, icon: Icon }, index) => {
        const done = currentStep > step;
        const active = currentStep === step;

        return (
          <li className="flex flex-1 items-center gap-2" key={step}>
            <span
              className={cn(
                "grid size-8 shrink-0 place-items-center rounded-full border transition-colors",
                active
                  ? "border-transparent bg-gradient-to-r from-brand-primary to-brand-accent text-primary-foreground shadow-sm shadow-brand-primary/25"
                  : done
                    ? "border-brand-primary/30 bg-brand-primary/10 text-brand-primary"
                    : "border-border bg-surface-raised text-text-muted",
              )}
            >
              <Icon className="size-4" weight={active || done ? "fill" : "regular"} />
            </span>
            <span
              className={cn(
                "text-xs font-medium whitespace-nowrap",
                active ? "text-foreground" : "text-text-muted",
              )}
            >
              {step}. {label}
            </span>
            {index < steps.length - 1 ? (
              <span
                className={cn(
                  "mx-1 hidden h-px flex-1 sm:block",
                  done ? "bg-brand-primary/40" : "bg-border",
                )}
              />
            ) : null}
          </li>
        );
      })}
    </ol>
  );
}

function resolveUploadStep(items: UploadQueueItem[]): number {
  if (items.length === 0) return 1;
  if (items.some((item) => item.status === "uploaded")) return 3;
  if (
    items.some((item) => isActiveUploadStatus(item.status)) ||
    items.some((item) => item.status === "pending" || item.status === "failed")
  ) {
    return 2;
  }
  return 1;
}

function UploadQueueRow({
  busy,
  item,
  onRemove,
  onRetry,
}: {
  busy: boolean;
  item: UploadQueueItem;
  onRemove: () => void;
  onRetry: () => void;
}) {
  const active = isActiveUploadStatus(item.status);
  const failed = item.status === "failed";
  const uploaded = item.status === "uploaded";

  return (
    <article className="grid gap-3 border-b border-border/80 px-3 py-3 last:border-b-0 md:grid-cols-[1fr_minmax(180px,260px)_auto] md:items-center">
      <div className="min-w-0">
        <div className="flex items-center gap-2">
          {uploaded ? (
            <CheckCircle className="size-4 text-status-success" />
          ) : (
            <FileArrowUp className="size-4 text-text-muted" />
          )}
          <p className="truncate text-sm font-medium text-foreground">
            {item.name}
          </p>
        </div>
        <p className="mt-1 text-xs text-text-muted">
          {formatFileSize(item.size)}
          {item.type ? ` · ${item.type}` : ""}
        </p>
        {item.documentId || item.taskId ? (
          <p className="mt-1 whitespace-pre-wrap text-xs text-text-muted">
            {item.documentId ? `document_id: ${item.documentId}` : ""}
            {item.documentId && item.taskId ? "\n" : ""}
            {item.taskId ? `task_id: ${item.taskId}` : ""}
            {item.taskStatus ? `\n${item.taskStatus}` : ""}
          </p>
        ) : null}
        {uploaded && item.taskStatus ? (
          <p className="mt-1 text-xs text-text-muted">已上传，后台处理中</p>
        ) : null}
        {item.error ? (
          <p className="mt-1 text-xs text-status-danger">{item.error}</p>
        ) : null}
      </div>

      <div className="grid gap-2">
        <span
          className={cn(
            "w-fit rounded-full border px-2 py-0.5 text-xs",
            failed
              ? "border-status-danger/30 bg-status-danger/10 text-status-danger"
              : uploaded
                ? "border-status-success/30 bg-status-success/10 text-status-success"
                : active
                  ? "border-primary/30 bg-primary/10 text-primary"
                  : "border-border bg-muted text-text-muted",
          )}
        >
          {uploadQueueStatusLabel(item.status)}
        </span>
        <Progress value={item.progress} label="上传进度" />
      </div>

      <div className="flex flex-wrap justify-end gap-2">
        {failed ? (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={onRetry}
            disabled={busy}
          >
            <ArrowCounterClockwise className="size-4" />
            重试
          </Button>
        ) : null}
        {!active ? (
          <Button
            type="button"
            variant="destructive"
            size="sm"
            onClick={onRemove}
            disabled={busy && item.status !== "failed"}
          >
            <Trash className="size-4" />
            移除
          </Button>
        ) : null}
      </div>
    </article>
  );
}

function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  if (size < 1024) return `${size} B`;
  if (size < 1024 * 1024) return `${(size / 1024).toFixed(1)} KB`;
  return `${(size / 1024 / 1024).toFixed(1)} MB`;
}
