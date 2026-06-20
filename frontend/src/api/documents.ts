import { ApiRequestError, type UserRole } from "./auth";
import { type ApiErrorResponse } from "./errors";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface DocumentSummary {
  id: string;
  title: string;
  original_file_name: string;
  brand: string | null;
  model: string | null;
  document_type: string | null;
  language: string | null;
  status: string;
  updated_at: string | null;
}

export interface DocumentFilters {
  brand: string;
  model: string;
  document_type: string;
  language: string;
  status: string;
}

export interface DocumentListResponse {
  total: number;
  items: DocumentSummary[];
}

export interface TaskResponse {
  document_id: string;
  task_id: string;
  status: string;
}

async function parseError(response: Response): Promise<ApiRequestError> {
  const payload = (await response.json()) as ApiErrorResponse;
  return new ApiRequestError(payload);
}

function authHeaders(token: string): HeadersInit {
  return { Authorization: `Bearer ${token}` };
}

function queryString(filters: DocumentFilters): string {
  const params = new URLSearchParams();
  Object.entries(filters).forEach(([key, value]) => {
    if (value.trim()) {
      params.set(key, value.trim());
    }
  });
  const text = params.toString();
  return text ? `?${text}` : "";
}

export async function listDocuments(token: string, filters: DocumentFilters): Promise<DocumentListResponse> {
  const response = await fetch(`${API_BASE_URL}/documents${queryString(filters)}`, {
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as DocumentListResponse;
}

export async function uploadDocument(token: string, file: File): Promise<TaskResponse> {
  const formData = new FormData();
  formData.append("file", file);
  const response = await fetch(`${API_BASE_URL}/documents`, {
    method: "POST",
    headers: authHeaders(token),
    body: formData
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as TaskResponse;
}

export async function reprocessDocument(token: string, documentId: string): Promise<TaskResponse> {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}/reprocess`, {
    method: "POST",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as TaskResponse;
}

export async function deleteDocument(token: string, documentId: string): Promise<{ document_id: string; status: string }> {
  const response = await fetch(`${API_BASE_URL}/documents/${documentId}`, {
    method: "DELETE",
    headers: authHeaders(token)
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as { document_id: string; status: string };
}

export function canMutateLibrary(role: UserRole): boolean {
  return role === "admin" || role === "maintainer";
}
