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

export type DocumentSummary = {
  id: string;
  title: string;
  original_file_name: string;
  brand: string | null;
  model: string | null;
  document_type: string | null;
  language: string | null;
  status: string;
  updated_at: string | null;
};

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

function authHeaders(token: string): HeadersInit {
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

export async function currentUser(token: string): Promise<CurrentUser> {
  const response = await fetch("/backend/auth/me", {
    headers: authHeaders(token),
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as CurrentUser;
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

export async function uploadDocument(token: string, file: File): Promise<TaskResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch("/backend/documents", {
    method: "POST",
    headers: authHeaders(token),
    body: formData,
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as TaskResponse;
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
