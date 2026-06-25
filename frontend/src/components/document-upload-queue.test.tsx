import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  DocumentUploadQueue,
  uploadQueueStatusLabel,
  validateUploadFile,
  type UploadQueueItem,
} from "./document-upload-queue";

const items: UploadQueueItem[] = [
  {
    id: "pending-1",
    name: "m7040-manual.pdf",
    size: 1024,
    type: "application/pdf",
    status: "pending",
    progress: 0,
  },
  {
    id: "uploading-1",
    name: "fault-codes.xlsx",
    size: 2048,
    type: "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    status: "uploading",
    progress: 62,
  },
  {
    id: "uploaded-1",
    name: "hydraulic.txt",
    size: 512,
    type: "text/plain",
    status: "uploaded",
    progress: 100,
    documentId: "doc-123",
    taskId: "task-456",
    taskStatus: "queued",
  },
  {
    id: "failed-1",
    name: "unsupported.exe",
    size: 256,
    type: "application/octet-stream",
    status: "failed",
    progress: 100,
    error: "不支持的文件类型",
  },
];

describe("document upload queue", () => {
  it("renders multi-file drag and click upload controls", () => {
    const html = renderToStaticMarkup(
      <DocumentUploadQueue
        items={[]}
        onFilesSelected={() => {}}
        onRemove={() => {}}
        onRetry={() => {}}
        onStartUpload={() => {}}
        onRequestClose={() => {}}
      />,
    );

    expect(html).toContain("拖拽多份资料到这里");
    expect(html).toContain("可点击选择文件，也可直接拖拽到这里。");
    expect(html).toContain('type="file"');
    expect(html).toContain("multiple");
  });

  it("renders each file status, progress, result and failure actions", () => {
    const html = renderToStaticMarkup(
      <DocumentUploadQueue
        items={items}
        onFilesSelected={() => {}}
        onRemove={() => {}}
        onRetry={() => {}}
        onStartUpload={() => {}}
        onRequestClose={() => {}}
      />,
    );

    expect(html).toContain("待开始上传");
    expect(html).toContain("已加入队列，点击“开始上传”后才会真正上传。");
    expect(html).toContain("开始上传（2）");
    expect(html).toContain("上传中");
    expect(html).toContain("62%");
    expect(html).toContain("document_id: doc-123");
    expect(html).toContain("task_id: task-456");
    expect(html).toContain("queued");
    expect(html).toContain("已上传，后台处理中");
    expect(html).toContain("不支持的文件类型");
    expect(html).toContain("重试");
    expect(html).toContain("移除");
  });

  it("keeps framed queue panels while reducing nested card clutter", () => {
    const html = renderToStaticMarkup(
      <DocumentUploadQueue
        items={items}
        onFilesSelected={() => {}}
        onRemove={() => {}}
        onRetry={() => {}}
        onStartUpload={() => {}}
        onRequestClose={() => {}}
      />,
    );

    expect(html).toContain(
      "rounded-2xl border border-border bg-surface-panel/65",
    );
    expect(html).toContain(
      "rounded-2xl border border-border/70 bg-surface-raised/80",
    );
    expect(html).toContain("border-b border-border/80");
    expect(html).not.toContain("bg-white/70");
  });

  it("keeps partial upload success and failure visible in the same queue", () => {
    const html = renderToStaticMarkup(
      <DocumentUploadQueue
        items={[items[2], items[3]]}
        onFilesSelected={() => {}}
        onRemove={() => {}}
        onRetry={() => {}}
        onStartUpload={() => {}}
        onRequestClose={() => {}}
      />,
    );

    expect(html).toContain("已上传");
    expect(html).toContain("document_id: doc-123");
    expect(html).toContain("上传失败");
    expect(html).toContain("不支持的文件类型");
    expect(html).toContain("重试");
  });

  it("shows a close confirmation when uploading files are active", () => {
    const html = renderToStaticMarkup(
      <DocumentUploadQueue
        items={items}
        closeConfirmationOpen
        onCancelClose={() => {}}
        onConfirmClose={() => {}}
        onFilesSelected={() => {}}
        onRemove={() => {}}
        onRetry={() => {}}
        onStartUpload={() => {}}
        onRequestClose={() => {}}
      />,
    );

    expect(html).toContain("上传中关闭队列需要确认");
    expect(html).toContain("继续上传");
    expect(html).toContain("确认关闭");
  });

  it("labels all required upload statuses", () => {
    expect(uploadQueueStatusLabel("pending")).toBe("待开始上传");
    expect(uploadQueueStatusLabel("validating")).toBe("校验中");
    expect(uploadQueueStatusLabel("uploading")).toBe("上传中");
    expect(uploadQueueStatusLabel("uploaded")).toBe("已上传");
    expect(uploadQueueStatusLabel("failed")).toBe("上传失败");
    expect(uploadQueueStatusLabel("retrying")).toBe("重试中");
  });

  it("validates supported document and image file extensions before upload", () => {
    expect(
      validateUploadFile({ name: "manual.PDF", type: "application/pdf" }),
    ).toBeNull();
    expect(
      validateUploadFile({ name: "fault-photo.webp", type: "image/webp" }),
    ).toBeNull();
    expect(
      validateUploadFile({
        name: "installer.exe",
        type: "application/octet-stream",
      }),
    ).toBe("不支持的文件类型");
  });
});
