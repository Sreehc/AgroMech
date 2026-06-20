"use client";

/* eslint-disable react-hooks/set-state-in-effect */

import { FormEvent, useEffect, useState } from "react";

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
  const [filters, setFilters] = useState<DocumentFilters>(emptyDocumentFilters);
  const [draftFilters, setDraftFilters] = useState<DocumentFilters>(emptyDocumentFilters);
  const [loading, setLoading] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [confirmation, setConfirmation] = useState<{ action: "reprocess" | "delete"; document: DocumentSummary } | null>(null);

  const canMutate = session ? canMutateLibrary(session.role) : false;

  async function load(nextSession = session, nextFilters = filters) {
    if (!nextSession) return;
    setLoading(true);
    setError(null);
    try {
      const response = await listDocuments(nextSession.token, nextFilters);
      setDocuments(response.items);
      setTotal(response.total);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "资料列表加载失败。");
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
    void load(session, draftFilters);
  }

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!session || !selectedFile || uploading) return;
    setUploading(true);
    setError(null);
    setMessage(null);
    try {
      const result = await uploadDocument(session.token, selectedFile);
      setMessage(`document_id: ${result.document_id}\ntask_id: ${result.task_id}`);
      setSelectedFile(null);
      await load(session, filters);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "上传失败。");
    } finally {
      setUploading(false);
    }
  }

  async function confirmAction() {
    if (!session || !confirmation) return;
    setError(null);
    setMessage(null);
    try {
      if (confirmation.action === "reprocess") {
        const result = await reprocessDocument(session.token, confirmation.document.id);
        setMessage(`document_id: ${result.document_id}\ntask_id: ${result.task_id}`);
      } else {
        const result = await deleteDocument(session.token, confirmation.document.id);
        setMessage(`document_id: ${result.document_id}\nstatus: ${result.status}`);
      }
      setConfirmation(null);
      await load(session, filters);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "操作失败。");
    }
  }

  return (
    <div className="flex min-h-0 flex-1 flex-col gap-5 overflow-auto p-4 md:p-6">
      <header>
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#60704d]">Library</p>
        <h2 className="mt-2 text-2xl font-semibold text-[#172016]">资料库</h2>
      </header>

      <form className="grid gap-3 rounded-lg border border-[#d8dfd0] bg-white/80 p-4 md:grid-cols-6" onSubmit={submitFilters}>
        {(["brand", "model", "document_type", "language", "status"] as const).map((field) => (
          <label className="grid gap-1 text-sm" key={field}>
            <span className="text-[#60704d]">{filterLabel(field)}</span>
            <input className="rounded-md border border-[#cbd6c0] px-3 py-2" value={draftFilters[field]} onChange={(event) => setDraftFilters({ ...draftFilters, [field]: event.target.value })} />
          </label>
        ))}
        <button className="self-end rounded-md bg-[#253322] px-4 py-2 text-white" type="submit">筛选</button>
      </form>

      {canMutate ? (
        <form className="flex flex-col gap-3 rounded-lg border border-[#d8dfd0] bg-white/80 p-4 md:flex-row md:items-end" onSubmit={submitUpload}>
          <label className="grid flex-1 gap-1 text-sm">
            <span className="text-[#60704d]">选择资料文件</span>
            <input type="file" onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)} />
          </label>
          <button className="rounded-md border border-[#cbd6c0] px-4 py-2 disabled:opacity-50" type="submit" disabled={!selectedFile || uploading}>
            {uploading ? "上传中" : "上传资料"}
          </button>
        </form>
      ) : null}

      {message ? <pre className="rounded-lg border border-emerald-200 bg-emerald-50 p-3 text-sm text-emerald-800">{message}</pre> : null}
      {error ? <p className="rounded-lg bg-red-50 p-3 text-sm text-red-700">{error}</p> : null}

      <section className="rounded-lg border border-[#d8dfd0] bg-white/80">
        <div className="flex items-center justify-between border-b border-[#d8dfd0] px-4 py-3 text-sm text-[#60704d]">
          <span>共 {total} 份资料</span>
          {loading ? <span>加载中</span> : null}
        </div>
        <div className="divide-y divide-[#e1e7da]">
          {documents.length === 0 && !loading ? <p className="p-6 text-sm text-[#60704d]">暂无资料。</p> : null}
          {documents.map((document) => (
            <article className="grid gap-3 p-4 md:grid-cols-[1.5fr_1fr_auto]" key={document.id}>
              <div>
                <h3 className="font-medium text-[#172016]">{document.title}</h3>
                <p className="text-sm text-[#60704d]">{document.original_file_name}</p>
              </div>
              <div className="text-sm text-[#60704d]">
                <p>{[document.brand, document.model].filter(Boolean).join(" / ") || "未标注品牌型号"}</p>
                <p>{[document.document_type, document.language].filter(Boolean).join(" / ") || "未标注类型语言"}</p>
                <p>{document.updated_at ?? "未更新"}</p>
              </div>
              <div className="flex items-center gap-2">
                <span className="rounded-full border border-[#cbd6c0] px-2.5 py-1 text-xs text-[#52614a]">{document.status}</span>
                {canMutate ? (
                  <>
                    <button className="rounded-md border border-[#cbd6c0] px-3 py-1.5 text-sm" type="button" onClick={() => setConfirmation({ action: "reprocess", document })}>重新处理</button>
                    <button className="rounded-md border border-red-200 px-3 py-1.5 text-sm text-red-700" type="button" onClick={() => setConfirmation({ action: "delete", document })}>删除</button>
                  </>
                ) : null}
              </div>
            </article>
          ))}
        </div>
      </section>

      {confirmation ? (
        <div className="fixed inset-0 z-50 grid place-items-center bg-black/30 p-4">
          <section className="w-full max-w-md rounded-lg bg-white p-5 shadow-lg">
            <h2 className="text-lg font-semibold">{confirmation.action === "reprocess" ? "确认重新处理" : "确认删除"}</h2>
            <p className="mt-2 text-sm text-[#60704d]">{confirmation.document.title}</p>
            <div className="mt-5 flex justify-end gap-2">
              <button className="rounded-md border border-[#cbd6c0] px-3 py-2 text-sm" type="button" onClick={() => setConfirmation(null)}>取消</button>
              <button className="rounded-md bg-[#253322] px-3 py-2 text-sm text-white" type="button" onClick={confirmAction}>确认</button>
            </div>
          </section>
        </div>
      ) : null}
    </div>
  );
}

function filterLabel(field: keyof DocumentFilters): string {
  return {
    brand: "品牌",
    model: "型号",
    document_type: "类型",
    language: "语言",
    status: "状态",
  }[field];
}
