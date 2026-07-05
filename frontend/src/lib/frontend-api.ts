export type UserRole = "admin" | "maintainer" | "user" | "evaluator";

export type CurrentUser = {
  username: string;
  role: UserRole;
};

export type LoginResponse = {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
};

export type ChatSessionMessage = Record<string, unknown>;

export type ChatSessionFilters = Record<string, unknown>;

export type ChatSession = {
  id: string;
  title: string;
  messages: ChatSessionMessage[];
  filters: ChatSessionFilters;
  has_image: boolean;
  created_at: string;
  updated_at: string;
};

export type ChatSessionListResponse = {
  total: number;
  items: ChatSession[];
};

export type ChatSessionCreateInput = {
  title?: string;
  messages?: ChatSessionMessage[];
  filters?: ChatSessionFilters;
  has_image?: boolean;
};

export type ChatSessionUpdateInput = {
  title?: string;
  messages?: ChatSessionMessage[];
  filters?: ChatSessionFilters;
  has_image?: boolean;
};

export type ApiErrorResponse = {
  error: {
    code: string;
    message: string;
    details: unknown;
    trace_id: string;
  };
};

export class ApiRequestError extends Error {
  response: ApiErrorResponse;

  constructor(response: ApiErrorResponse) {
    super(response.error.message);
    this.response = response;
  }
}

export type DocumentVisibility = "public" | "private";

export type DocumentSummary = {
  id: string;
  title: string;
  original_file_name: string;
  brand: string | null;
  model: string | null;
  document_type: string | null;
  language: string | null;
  status: string;
  visibility: DocumentVisibility;
  owner_user_id: string | null;
  updated_at: string | null;
  summary?: string | null;
  recent_task?: Pick<DocumentTaskSummary, "id" | "task_type" | "status" | "stage"> | null;
  failure?: DocumentFailure | null;
};

export type DocumentMetadata = {
  brand: string | null;
  model: string | null;
  document_type: string | null;
  language: string | null;
  source: string | null;
  original_file_name: string;
  mime_type: string | null;
  file_size_bytes: number | null;
};

export type DocumentFailure = {
  stage: string | null;
  code: string | null;
  message: string | null;
};

export type DocumentTaskSummary = {
  id: string;
  document_id: string;
  task_type: string;
  status: string;
  attempt_count: number;
  stage: string | null;
  error_code: string | null;
  error_message: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export function documentTaskStagePresentation(stage: string | null): string {
  if (!stage) return "未标注";
  const labels: Record<string, string> = {
    queued: "已排队",
    processing: "处理中",
    uploading: "上传中",
    ocr: "OCR 中",
    chunking: "文本切分中",
    text_embedding: "文本向量化中",
    visual_embedding: "页面向量化中",
    indexed: "入库完成",
    deleted: "已删除",
    parse: "解析中",
    render: "渲染中",
    vision: "视觉理解中",
  };
  return labels[stage] ?? stage;
}

export type DocumentChunkSummary = {
  id: string;
  chunk_type: string;
  summary: string | null;
  page_number: number | null;
  section_title: string | null;
};

export type DocumentDetail = {
  id: string;
  title: string;
  metadata: DocumentMetadata;
  status: string;
  visibility: DocumentVisibility;
  owner_user_id: string | null;
  failure: DocumentFailure;
  recent_task: DocumentTaskSummary | null;
  chunks: DocumentChunkSummary[];
  updated_at?: string | null;
};

export const documentStatuses = [
  "queued",
  "processing",
  "indexed",
  "failed",
  "reprocessing",
  "deleting",
  "deleted",
] as const;

export type DocumentStatus = (typeof documentStatuses)[number];

export type DocumentStatusTone = "neutral" | "info" | "success" | "warning" | "danger";

export type DocumentStatusPresentation = {
  label: string;
  tone: DocumentStatusTone;
  known: boolean;
};

const documentStatusPresentations: Record<DocumentStatus, DocumentStatusPresentation> = {
  queued: { label: "已排队", tone: "info", known: true },
  processing: { label: "处理中", tone: "info", known: true },
  indexed: { label: "入库完成", tone: "success", known: true },
  failed: { label: "处理失败", tone: "danger", known: true },
  reprocessing: { label: "重新处理中", tone: "warning", known: true },
  deleting: { label: "删除中", tone: "warning", known: true },
  deleted: { label: "已删除", tone: "neutral", known: true },
};

export function isKnownDocumentStatus(status: string): status is DocumentStatus {
  return documentStatuses.includes(status as DocumentStatus);
}

export function documentStatusPresentation(status: string): DocumentStatusPresentation {
  if (isKnownDocumentStatus(status)) {
    return documentStatusPresentations[status];
  }
  return { label: "未知状态", tone: "neutral", known: false };
}

export type DocumentFilters = {
  brand: string;
  model: string;
  document_type: string;
  language: string;
  status: string;
};

export type DocumentListResponse = {
  total: number;
  items: DocumentSummary[];
};

export type TaskResponse = {
  document_id: string;
  task_id: string;
  status: string;
};

export type UploadDocumentOptions = {
  onProgress?: (percent: number) => void;
  visibility?: DocumentVisibility;
};

export type DocumentPreviewType = "text" | "pdf" | "unavailable";

export type DocumentSourcePosition = {
  page_number: number | null;
  section_title: string | null;
  worksheet_name: string | null;
  row_start: number | null;
  row_end: number | null;
};

export type DocumentPreviewHighlight = {
  type: "text" | "area";
  text?: string;
  page_number?: number | null;
  source_locator?: Record<string, unknown>;
  bbox?: {
    x: number;
    y: number;
    width: number;
    height: number;
  };
};

export type DocumentPreviewResponse = {
  document_id: string;
  document_title: string;
  chunk_id: string | null;
  preview_type: DocumentPreviewType;
  accessible: boolean;
  source_locator: Record<string, unknown>;
  source_position: DocumentSourcePosition;
  evidence_snippet: string | null;
  text_preview: string | null;
  pdf_page: {
    page_number: number | null;
    page_image_url: string | null;
    render_status: "not_rendered" | "rendered" | "failed" | "missing";
  } | null;
  highlights: DocumentPreviewHighlight[];
  unavailable_reason: string | null;
};

export type RetrievalTraceChannelStatus = {
  channel: string;
  reason: string;
};

export type RetrievalTraceChannels = {
  used: string[];
  degraded: RetrievalTraceChannelStatus[];
  embedding_version?: string;
};

export type RetrievalTraceRerankItem = {
  chunk_id: string;
  before_rank: number;
  after_rank: number;
  before_score?: number;
  after_score?: number;
  channels?: string[];
  factors?: Record<string, number>;
};

export type RetrievalTrace = {
  trace_id: string;
  query: string;
  filters: Record<string, unknown>;
  channels: RetrievalTraceChannels;
  model_config: Record<string, unknown>;
  created_at: string | null;
  candidates?: Array<Record<string, unknown>>;
  rerank?: {
    strategy?: string;
    fallback?: boolean;
    items: RetrievalTraceRerankItem[];
  };
  final_evidence: Array<Record<string, unknown>>;
};

export const emptyDocumentFilters: DocumentFilters = {
  brand: "",
  model: "",
  document_type: "",
  language: "",
  status: "",
};

export function documentQueryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value.trim()) {
      params.set(key, value.trim());
    }
  });
  const query = params.toString();
  return query ? `?${query}` : "";
}

export function canMutateLibrary(role: UserRole): boolean {
  return role === "admin" || role === "maintainer";
}

// 谁能上传资料：admin/maintainer 可传公用库，user 只能传自己私库。evaluator 只读。
export function canUploadDocuments(role: UserRole): boolean {
  return role === "admin" || role === "maintainer" || role === "user";
}

// 谁能把资料发布到公用库：仅 admin/maintainer。其余角色只能建私有资料。
export function canPublishToPublicLibrary(role: UserRole): boolean {
  return role === "admin" || role === "maintainer";
}

// 能否管理（重新处理 / 删除）某份资料。列表接口只会返回公用资料与本人的私有
// 资料，因此能上传的用户看到的任何「私有」资料都归其本人所有，可安全放开管理
// 入口；后端仍会做归属校验兜底。evaluator 只读，不能管理任何资料。
export function canManageDocument(role: UserRole, visibility: DocumentVisibility): boolean {
  if (role === "admin" || role === "maintainer") return true;
  return visibility === "private" && canUploadDocuments(role);
}

export function errorMessage(error: ApiErrorResponse): string {
  return `${error.error.code}: ${error.error.message}`;
}

async function parseError(response: Response): Promise<ApiRequestError> {
  try {
    return new ApiRequestError((await response.json()) as ApiErrorResponse);
  } catch {
    return new ApiRequestError({
      error: {
        code: "request_failed",
        message: response.statusText || "Request failed",
        details: null,
        trace_id: "",
      },
    });
  }
}

function authHeaders(token: string): Record<string, string> {
  return { Authorization: `Bearer ${token}` };
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch("/backend/auth/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password }),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as LoginResponse;
}

export async function register(
  username: string,
  password: string,
  displayName?: string,
): Promise<LoginResponse> {
  const response = await fetch("/backend/auth/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      username,
      password,
      ...(displayName ? { display_name: displayName } : {}),
    }),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as LoginResponse;
}

export async function currentUser(token: string): Promise<CurrentUser> {
  const response = await fetch("/backend/auth/me", {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as CurrentUser;
}

export async function listChatSessions(token: string, limit = 50): Promise<ChatSessionListResponse> {
  const params = new URLSearchParams();
  params.set("limit", String(limit));
  const response = await fetch(`/backend/chat-sessions?${params.toString()}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as ChatSessionListResponse;
}

export async function createChatSession(token: string, payload: ChatSessionCreateInput): Promise<ChatSession> {
  const response = await fetch("/backend/chat-sessions", {
    method: "POST",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as ChatSession;
}

export async function getChatSession(token: string, sessionId: string): Promise<ChatSession> {
  const response = await fetch(`/backend/chat-sessions/${sessionId}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as ChatSession;
}

export async function updateChatSession(
  token: string,
  sessionId: string,
  payload: ChatSessionUpdateInput,
): Promise<ChatSession> {
  const response = await fetch(`/backend/chat-sessions/${sessionId}`, {
    method: "PATCH",
    headers: { ...authHeaders(token), "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as ChatSession;
}

export async function deleteChatSession(token: string, sessionId: string): Promise<{ session_id: string; deleted: boolean }> {
  const response = await fetch(`/backend/chat-sessions/${sessionId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as { session_id: string; deleted: boolean };
}

export async function listDocuments(token: string, filters: DocumentFilters): Promise<DocumentListResponse> {
  const response = await fetch(`/backend/documents${documentQueryString(filters)}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as DocumentListResponse;
}

export async function getDocument(token: string, documentId: string): Promise<DocumentDetail> {
  const response = await fetch(`/backend/documents/${documentId}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as DocumentDetail;
}

export async function uploadDocument(token: string, file: File, options: UploadDocumentOptions = {}): Promise<TaskResponse> {
  const formData = new FormData();
  formData.append("file", file);
  if (options.visibility) {
    formData.append("visibility", options.visibility);
  }
  return await new Promise<TaskResponse>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", "/backend/documents");
    for (const [name, value] of Object.entries(authHeaders(token))) {
      xhr.setRequestHeader(name, value);
    }
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable || !options.onProgress) return;
      const percent = Math.max(0, Math.min(100, Math.round((event.loaded / event.total) * 100)));
      options.onProgress(percent);
    };
    xhr.onreadystatechange = async () => {
      if (xhr.readyState !== 4) return;
      const response = new Response(xhr.responseText, {
        status: xhr.status,
        statusText: xhr.statusText,
        headers: { "Content-Type": "application/json" },
      });
      if (!response.ok) {
        reject(await parseError(response));
        return;
      }
      resolve((await response.json()) as TaskResponse);
    };
    xhr.onerror = async () => {
      reject(
        await parseError(
          new Response(null, {
            status: 0,
            statusText: "Network error",
          }),
        ),
      );
    };
    xhr.send(formData);
  });
}

export async function reprocessDocument(token: string, documentId: string): Promise<TaskResponse> {
  const response = await fetch(`/backend/documents/${documentId}/reprocess`, {
    method: "POST",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as TaskResponse;
}

export async function deleteDocument(token: string, documentId: string): Promise<{ document_id: string; status: string }> {
  const response = await fetch(`/backend/documents/${documentId}`, {
    method: "DELETE",
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as { document_id: string; status: string };
}

export async function getDocumentPreview(
  token: string,
  documentId: string,
  chunkId?: string,
): Promise<DocumentPreviewResponse> {
  const params = new URLSearchParams();
  if (chunkId) {
    params.set("chunk_id", chunkId);
  }
  const query = params.toString();
  const response = await fetch(`/backend/documents/${documentId}/preview${query ? `?${query}` : ""}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as DocumentPreviewResponse;
}

export async function getRetrievalTrace(token: string, traceId: string): Promise<RetrievalTrace> {
  const response = await fetch(`/backend/retrieval-traces/${traceId}`, {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as RetrievalTrace;
}
