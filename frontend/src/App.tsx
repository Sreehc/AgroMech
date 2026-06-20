import { FormEvent, useEffect, useMemo, useState } from "react";

import { ApiRequestError, currentUser, login } from "./api/auth";
import {
  canMutateLibrary,
  deleteDocument,
  listDocuments,
  reprocessDocument,
  uploadDocument,
  type DocumentFilters,
  type DocumentSummary
} from "./api/documents";
import { errorMessage } from "./api/errors";
import {
  askImageQuestion,
  askTextQuestion,
  getRetrievalTrace,
  type Citation,
  type ImageQaResponse,
  type QaFilters,
  type QaResponse,
  type RetrievalTrace
} from "./api/qa";
import {
  canMaintainLibrary,
  clearSession,
  loadSession,
  saveSession,
  type Session
} from "./auth/session";
import "./styles.css";

function guardedInitialPath(session: Session | null): string {
  const path = window.location.pathname;
  if (!session && path !== "/login") {
    window.history.replaceState({}, "", "/login");
    return "/login";
  }
  if (session && (path === "/" || path === "/login")) {
    window.history.replaceState({}, "", "/qa");
    return "/qa";
  }
  return path;
}

function navigate(path: string, setPath: (path: string) => void): void {
  window.history.pushState({}, "", path);
  setPath(path);
}

function LoginPage({
  onAuthenticated
}: {
  onAuthenticated: (session: Session) => void;
}) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const disabled = username.trim() === "" || password === "" || submitting;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) {
      return;
    }

    setSubmitting(true);
    setError(null);
    try {
      const token = await login(username.trim(), password);
      const user = await currentUser(token.access_token);
      onAuthenticated({
        token: token.access_token,
        username: user.username,
        role: user.role
      });
    } catch (caught) {
      if (caught instanceof ApiRequestError) {
        setError(errorMessage(caught.response));
      } else {
        setError("服务暂时不可用，请稍后重试。");
      }
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="login-shell">
      <section className="login-panel" aria-labelledby="login-title">
        <p className="eyebrow">AgroMech RAG</p>
        <h1 id="login-title">登录</h1>
        <form className="login-form" onSubmit={submit}>
          <label>
            <span>账号</span>
            <input
              autoComplete="username"
              value={username}
              onChange={(event) => setUsername(event.target.value)}
            />
          </label>
          <label>
            <span>密码</span>
            <input
              autoComplete="current-password"
              type="password"
              value={password}
              onChange={(event) => setPassword(event.target.value)}
            />
          </label>
          {error ? (
            <p className="form-error" role="alert">
              {error}
            </p>
          ) : null}
          <button type="submit" disabled={disabled}>
            {submitting ? "登录中" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}

const emptyFilters: DocumentFilters = {
  brand: "",
  model: "",
  document_type: "",
  language: "",
  status: ""
};

const emptyQaFilters: QaFilters = {
  brand: "",
  model: "",
  document_type: "",
  subsystem: "",
  language: ""
};

function LibraryPage({ session }: { session: Session }) {
  const [documents, setDocuments] = useState<DocumentSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [filters, setFilters] = useState<DocumentFilters>(emptyFilters);
  const [draftFilters, setDraftFilters] = useState<DocumentFilters>(emptyFilters);
  const [loading, setLoading] = useState(true);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploading, setUploading] = useState(false);
  const [duplicateDocumentId, setDuplicateDocumentId] = useState<string | null>(null);
  const [confirmation, setConfirmation] = useState<{
    action: "reprocess" | "delete";
    document: DocumentSummary;
  } | null>(null);
  const canMutate = canMutateLibrary(session.role);

  async function loadDocuments(nextFilters = filters) {
    setLoading(true);
    setError(null);
    try {
      const response = await listDocuments(session.token, nextFilters);
      setDocuments(response.items);
      setTotal(response.total);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "资料列表加载失败。");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    loadDocuments();
  }, [session.token]);

  function submitFilters(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setFilters(draftFilters);
    loadDocuments(draftFilters);
  }

  async function submitUpload(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedFile || uploading) {
      return;
    }
    setUploading(true);
    setError(null);
    setMessage(null);
    try {
      const result = await uploadDocument(session.token, selectedFile);
      setMessage(`document_id: ${result.document_id}\ntask_id: ${result.task_id}`);
      setSelectedFile(null);
      await loadDocuments();
    } catch (caught) {
      if (caught instanceof ApiRequestError && caught.response.error.code === "duplicate_of") {
        setDuplicateDocumentId(duplicateDocumentIdFrom(caught.response.error.details));
      } else {
        setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "上传失败。");
      }
    } finally {
      setUploading(false);
    }
  }

  async function confirmAction() {
    if (!confirmation) {
      return;
    }
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
      await loadDocuments();
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "操作失败。");
    }
  }

  return (
    <section className="library-page" aria-label="资料库工作区">
      <form className="library-filters" onSubmit={submitFilters}>
        {(["brand", "model", "document_type", "language", "status"] as const).map((field) => (
          <label key={field}>
            <span>{filterLabel(field)}</span>
            <input
              value={draftFilters[field]}
              onChange={(event) => setDraftFilters({ ...draftFilters, [field]: event.target.value })}
            />
          </label>
        ))}
        <button type="submit">筛选</button>
      </form>

      {canMutate ? (
        <form className="upload-strip" onSubmit={submitUpload}>
          <label>
            <span>选择资料文件</span>
            <input
              type="file"
              onChange={(event) => setSelectedFile(event.target.files?.[0] ?? null)}
            />
          </label>
          <button type="submit" disabled={!selectedFile || uploading}>
            {uploading ? "上传中" : "上传资料"}
          </button>
        </form>
      ) : null}

      {message ? (
        <div className="result-message">
          {message.split("\n").map((line) => (
            <span key={line}>{line}</span>
          ))}
        </div>
      ) : null}
      {error ? <p className="form-error">{error}</p> : null}

      <div className="library-summary">
        <span>共 {total} 份资料</span>
        {loading ? <span>加载中</span> : null}
      </div>

      <div className="document-table" role="table" aria-label="资料列表">
        <div className="document-row document-header" role="row">
          <span role="columnheader">资料</span>
          <span role="columnheader">元数据</span>
          <span role="columnheader">状态</span>
          <span role="columnheader">操作</span>
        </div>
        {documents.length === 0 && !loading ? (
          <p className="empty-state">暂无资料。</p>
        ) : null}
        {documents.map((document) => (
          <div className="document-row" role="row" key={document.id}>
            <div role="cell">
              <strong>{document.title}</strong>
              <span>{document.original_file_name}</span>
            </div>
            <div role="cell">
              <span>{metadataText(document)}</span>
              <span>{[document.document_type, document.language].filter(Boolean).join(" / ") || "未标注"}</span>
            </div>
            <div role="cell">
              <span className={`status-pill status-${document.status}`}>{document.status}</span>
              <span>{document.updated_at ?? "未更新"}</span>
            </div>
            <div className="row-actions" role="cell">
              {canMutate ? (
                <>
                  <button
                    type="button"
                    onClick={() => setConfirmation({ action: "reprocess", document })}
                    aria-label={`重新处理 ${document.title}`}
                  >
                    重新处理
                  </button>
                  <button
                    type="button"
                    onClick={() => setConfirmation({ action: "delete", document })}
                    aria-label={`删除 ${document.title}`}
                  >
                    删除
                  </button>
                </>
              ) : (
                <span>只读</span>
              )}
            </div>
          </div>
        ))}
      </div>

      {duplicateDocumentId !== null ? (
        <div className="modal-backdrop">
          <section className="modal" role="dialog" aria-modal="true" aria-label="重复资料">
            <h2>重复资料</h2>
            <p>系统检测到相同文件，已存在资料 {duplicateDocumentId || "未知"}。</p>
            <div className="modal-actions">
              <button type="button" onClick={() => setDuplicateDocumentId(null)}>
                取消
              </button>
              <button type="button" onClick={() => setDuplicateDocumentId(null)}>
                继续上传为新版本
              </button>
            </div>
          </section>
        </div>
      ) : null}

      {confirmation ? (
        <div className="modal-backdrop">
          <section
            className="modal"
            role="dialog"
            aria-modal="true"
            aria-label={confirmation.action === "reprocess" ? "确认重新处理" : "确认删除"}
          >
            <h2>{confirmation.action === "reprocess" ? "确认重新处理" : "确认删除"}</h2>
            <p>
              {confirmation.action === "reprocess"
                ? `重新处理 ${confirmation.document.title} 会创建新的处理任务。`
                : `删除 ${confirmation.document.title} 会影响资料、chunk、索引和后续检索。`}
            </p>
            <div className="modal-actions">
              <button type="button" onClick={() => setConfirmation(null)}>
                取消
              </button>
              <button type="button" onClick={confirmAction}>
                {confirmation.action === "reprocess" ? "确认重新处理" : "确认删除"}
              </button>
            </div>
          </section>
        </div>
      ) : null}
    </section>
  );
}

function filterLabel(field: keyof DocumentFilters): string {
  return {
    brand: "品牌",
    model: "型号",
    document_type: "类型",
    language: "语言",
    status: "状态"
  }[field];
}

function metadataText(document: DocumentSummary): string {
  const brandModel = [document.brand, document.model].filter(Boolean).join(" / ");
  return brandModel || "未标注品牌型号";
}

function duplicateDocumentIdFrom(details: unknown): string {
  if (typeof details === "object" && details !== null && "document_id" in details) {
    return String((details as { document_id?: unknown }).document_id ?? "");
  }
  return "";
}

function QaPage({ session }: { session: Session }) {
  const [question, setQuestion] = useState("");
  const [filters, setFilters] = useState<QaFilters>(emptyQaFilters);
  const [answer, setAnswer] = useState<QaResponse | null>(null);
  const [trace, setTrace] = useState<RetrievalTrace | null>(null);
  const [traceOpen, setTraceOpen] = useState(false);
  const [statusText, setStatusText] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [loadingTrace, setLoadingTrace] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disabled = question.trim() === "" || submitting;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) {
      return;
    }
    setSubmitting(true);
    setStatusText("retrieving/generating");
    setError(null);
    setTrace(null);
    setTraceOpen(false);
    try {
      const response = await askTextQuestion(session.token, question.trim(), filters);
      setAnswer(response);
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "问答请求失败。");
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleTrace() {
    if (!answer) {
      return;
    }
    if (traceOpen) {
      setTraceOpen(false);
      return;
    }
    setTraceOpen(true);
    if (trace) {
      return;
    }
    setLoadingTrace(true);
    try {
      setTrace(await getRetrievalTrace(session.token, answer.trace_id));
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "检索链路加载失败。");
    } finally {
      setLoadingTrace(false);
    }
  }

  return (
    <section className="qa-page" aria-label="问答工作区">
      <form className="qa-form" onSubmit={submit}>
        <div className="qa-filters">
          {(["brand", "model", "document_type", "subsystem", "language"] as const).map((field) => (
            <label key={field}>
              <span>{qaFilterLabel(field)}</span>
              <input
                value={filters[field]}
                onChange={(event) => setFilters({ ...filters, [field]: event.target.value })}
              />
            </label>
          ))}
        </div>
        <label className="question-box">
          <span>问题</span>
          <textarea
            value={question}
            onChange={(event) => setQuestion(event.target.value)}
            rows={5}
          />
        </label>
        <button type="submit" disabled={disabled}>
          提交问题
        </button>
      </form>

      {statusText ? <p className="status-line">{statusText}</p> : null}
      {error ? <p className="form-error">{error}</p> : null}

      {answer ? (
        <section className="answer-layout">
          <div className="answer-panel">
            <h2>回答</h2>
            <p>{answer.answer}</p>
            <StructuredSections sections={answer.sections} />
          </div>
          <div className="citations-panel">
            <h2>引用</h2>
            {answer.citations.length === 0 ? <p>暂无引用。</p> : null}
            {answer.citations.map((citation) => (
              <CitationItem citation={citation} key={`${citation.document_id}-${citation.chunk_id}`} />
            ))}
          </div>
          <div className="trace-panel">
            <button type="button" onClick={toggleTrace}>
              查看检索链路
            </button>
            {traceOpen ? (
              <div className="trace-content">
                {loadingTrace ? <p>加载中</p> : null}
                {trace ? <TraceView trace={trace} /> : null}
              </div>
            ) : null}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function qaFilterLabel(field: keyof QaFilters): string {
  return {
    brand: "品牌",
    model: "型号",
    document_type: "类型",
    subsystem: "系统",
    language: "语言"
  }[field];
}

function StructuredSections({ sections }: { sections: Record<string, unknown> }) {
  return (
    <div className="structured-sections">
      {Object.entries(sections).map(([key, value]) => (
        <section key={key}>
          <h3>{sectionLabel(key)}</h3>
          <p>{formatValue(value)}</p>
        </section>
      ))}
    </div>
  );
}

function sectionLabel(key: string): string {
  return {
    conclusion: "结论",
    applicability: "适用范围",
    possible_causes: "可能原因",
    inspection_steps: "检查步骤",
    safety_reminder: "安全提醒",
    citations: "引用来源",
    uncertainty: "不确定性"
  }[key] ?? key;
}

function CitationItem({ citation }: { citation: Citation }) {
  return (
    <article className="citation-item">
      <strong>{citation.document_title}</strong>
      <span>{sourceLocatorText(citation.source_locator)}</span>
      <span>{citation.evidence_type} / {citation.chunk_id}</span>
      <p>{citation.evidence_snippet}</p>
    </article>
  );
}

function sourceLocatorText(sourceLocator: Record<string, unknown>): string {
  return Object.entries(sourceLocator)
    .map(([key, value]) => `${key}: ${String(value)}`)
    .join(" / ");
}

function TraceView({ trace }: { trace: RetrievalTrace }) {
  return (
    <div className="trace-view">
      <p>{trace.trace_id}</p>
      <p>{trace.channels.used.join(", ")}</p>
      {trace.channels.degraded.map((item) => (
        <p key={`${item.channel}-${item.reason}`}>{item.channel}: {item.reason}</p>
      ))}
      <div>
        <h3>候选</h3>
        {(trace.candidates ?? []).map((candidate) => (
          <p key={candidate.chunk_id}>{candidate.chunk_id}</p>
        ))}
      </div>
      <div>
        <h3>Rerank</h3>
        {(trace.rerank?.items ?? []).map((item) => (
          <p key={item.chunk_id}>{item.chunk_id} {item.before_rank} -&gt; {item.after_rank}</p>
        ))}
      </div>
      <div>
        <h3>最终证据</h3>
        {(trace.final_evidence ?? []).map((item) => (
          <p key={item.chunk_id}>{item.chunk_id}</p>
        ))}
      </div>
    </div>
  );
}

function formatValue(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map((item) => formatValue(item)).join("；");
  }
  if (typeof value === "object" && value !== null) {
    return JSON.stringify(value);
  }
  return String(value ?? "");
}

const supportedImageTypes = ["image/png", "image/jpeg", "image/jpg", "image/webp"];

function ImageQuestionPage({ session }: { session: Session }) {
  const [file, setFile] = useState<File | null>(null);
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const [question, setQuestion] = useState("");
  const [brand, setBrand] = useState("");
  const [model, setModel] = useState("");
  const [answer, setAnswer] = useState<ImageQaResponse | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const disabled = !file || submitting;

  function selectFiles(files: FileList | null) {
    setAnswer(null);
    setError(null);
    if (!files || files.length === 0) {
      setFile(null);
      setPreviewUrl(null);
      return;
    }
    if (files.length > 1) {
      setFile(null);
      setPreviewUrl(null);
      setError("一次只能上传一张图片。");
      return;
    }
    const nextFile = files[0];
    if (!supportedImageTypes.includes(nextFile.type)) {
      setFile(null);
      setPreviewUrl(null);
      setError("仅支持 PNG、JPG、JPEG、WEBP。");
      return;
    }
    setFile(nextFile);
    setPreviewUrl(globalThis.URL?.createObjectURL ? globalThis.URL.createObjectURL(nextFile) : "");
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!file || submitting) {
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      setAnswer(await askImageQuestion(session.token, file, { question, brand, model }));
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "图片问答请求失败。");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <section className="image-question-page" aria-label="图片提问工作区">
      <form className="image-question-form" onSubmit={submit}>
        <label className="image-upload">
          <span>上传图片</span>
          <input
            type="file"
            multiple
            onChange={(event) => selectFiles(event.target.files)}
          />
        </label>

        {file ? (
          <div className="image-preview">
            <img src={previewUrl ?? ""} alt="图片预览" />
            <div>
              <strong>{file.name}</strong>
              <span>{file.type}</span>
              <span>{Math.max(1, Math.round(file.size / 1024))} KB</span>
            </div>
          </div>
        ) : null}

        <div className="image-question-grid">
          <label>
            <span>品牌</span>
            <input value={brand} onChange={(event) => setBrand(event.target.value)} />
          </label>
          <label>
            <span>型号</span>
            <input value={model} onChange={(event) => setModel(event.target.value)} />
          </label>
        </div>

        <label className="question-box">
          <span>图片问题</span>
          <textarea value={question} onChange={(event) => setQuestion(event.target.value)} rows={4} />
        </label>

        <button type="submit" disabled={disabled}>
          {submitting ? "提交中" : "提交图片问题"}
        </button>
      </form>

      {error ? <p className="form-error">{error}</p> : null}

      {answer ? (
        <section className="answer-layout">
          <div className="answer-panel">
            <h2>视觉观察</h2>
            <p>{answer.visual_observation}</p>
            <p>{answer.ocr_text || "无 OCR 文本"}</p>
            <p>{answer.detected_entities.possible_models.join(", ") || "未识别型号"}</p>
            <p>{answer.detected_entities.visible_parts.join(", ") || "未识别部件"}</p>
            <p>{answer.visual_confidence.low_confidence ? "低置信" : `置信度 ${answer.visual_confidence.confidence}`}</p>
          </div>
          <div className="answer-panel">
            <h2>回答</h2>
            <p>{answer.answer}</p>
            <StructuredSections sections={answer.sections} />
          </div>
          <div className="citations-panel">
            <h2>引用</h2>
            {answer.citations.map((citation) => (
              <CitationItem citation={citation} key={`${citation.document_id}-${citation.chunk_id}`} />
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function Workspace({
  session,
  path,
  onNavigate,
  onLogout
}: {
  session: Session;
  path: string;
  onNavigate: (path: string) => void;
  onLogout: () => void;
}) {
  const title = useMemo(() => {
    if (path === "/image-question") {
      return "图片提问";
    }
    if (path === "/library") {
      return "资料库";
    }
    return "问答";
  }, [path]);

  return (
    <div className="workspace-shell">
      <aside className="sidebar" aria-label="主导航">
        <p className="eyebrow">AgroMech RAG</p>
        <nav>
          <a href="/qa" onClick={(event) => {
            event.preventDefault();
            onNavigate("/qa");
          }}>
            问答
          </a>
          <a href="/image-question" onClick={(event) => {
            event.preventDefault();
            onNavigate("/image-question");
          }}>
            图片提问
          </a>
          {canMaintainLibrary(session) ? (
            <a href="/library" onClick={(event) => {
              event.preventDefault();
              onNavigate("/library");
            }}>
              资料库
            </a>
          ) : null}
        </nav>
        <div className="user-block">
          <span>{session.username}</span>
          <button type="button" onClick={onLogout}>
            退出
          </button>
        </div>
      </aside>
      <main className="workspace-main">
        <header>
          <h1>{title}</h1>
        </header>
        {path === "/library" ? (
          <LibraryPage session={session} />
        ) : path === "/qa" ? (
          <QaPage session={session} />
        ) : path === "/image-question" ? (
          <ImageQuestionPage session={session} />
        ) : (
          <section className="placeholder-panel">
            <p>当前页面已受登录态保护。</p>
          </section>
        )}
      </main>
    </div>
  );
}

function App() {
  const [session, setSession] = useState<Session | null>(() => loadSession());
  const [path, setPath] = useState(() => guardedInitialPath(loadSession()));

  useEffect(() => {
    function handlePopState() {
      setPath(guardedInitialPath(loadSession()));
    }
    window.addEventListener("popstate", handlePopState);
    return () => window.removeEventListener("popstate", handlePopState);
  }, []);

  useEffect(() => {
    if (!session) {
      if (path !== "/login") {
        window.history.replaceState({}, "", "/login");
        setPath("/login");
      }
    }
  }, [path, session]);

  useEffect(() => {
    if (!session) {
      return;
    }
    let cancelled = false;
    currentUser(session.token)
      .then((user) => {
        if (cancelled) {
          return;
        }
        const refreshed = { token: session.token, username: user.username, role: user.role };
        if (user.username !== session.username || user.role !== session.role) {
          setSession(refreshed);
          saveSession(refreshed);
        }
      })
      .catch((caught) => {
        if (cancelled) {
          return;
        }
        if (caught instanceof ApiRequestError && caught.response.error.code === "unauthorized") {
          clearSession();
          setSession(null);
          window.history.replaceState({}, "", "/login");
          setPath("/login");
        }
      });

    return () => {
      cancelled = true;
    };
  }, [session?.token]);

  function handleAuthenticated(nextSession: Session) {
    saveSession(nextSession);
    setSession(nextSession);
    navigate("/qa", setPath);
  }

  function handleLogout() {
    clearSession();
    setSession(null);
    navigate("/login", setPath);
  }

  if (!session) {
    return <LoginPage onAuthenticated={handleAuthenticated} />;
  }

  return (
    <Workspace
      session={session}
      path={path}
      onNavigate={(nextPath) => navigate(nextPath, setPath)}
      onLogout={handleLogout}
    />
  );
}

export default App;
