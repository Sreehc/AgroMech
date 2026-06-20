import { ApiRequestError } from "./auth";
import { type ApiErrorResponse } from "./errors";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export interface QaFilters {
  brand: string;
  model: string;
  document_type: string;
  subsystem: string;
  language: string;
}

export interface Citation {
  document_id: string;
  document_title: string;
  chunk_id: string;
  source_locator: Record<string, unknown>;
  evidence_snippet: string;
  evidence_type: string;
  accessible: boolean;
}

export interface QaResponse {
  answer: string;
  sections: Record<string, unknown>;
  citations: Citation[];
  trace_id: string;
  uncertainty: { level: string; reasons: string[] };
  safety_warnings: string[];
}

export interface RetrievalTrace {
  trace_id: string;
  query: string;
  filters: Record<string, unknown>;
  channels: {
    used: string[];
    degraded: { channel: string; reason: string }[];
  };
  candidates?: { chunk_id: string; score?: number; channels?: string[] }[];
  rerank?: { items?: { chunk_id: string; before_rank: number; after_rank: number }[] };
  final_evidence?: { chunk_id: string; document_id?: string }[];
}

async function parseError(response: Response): Promise<ApiRequestError> {
  const payload = (await response.json()) as ApiErrorResponse;
  return new ApiRequestError(payload);
}

function authHeaders(token: string): HeadersInit {
  return {
    Authorization: `Bearer ${token}`,
    "Content-Type": "application/json"
  };
}

export async function askTextQuestion(token: string, question: string, filters: QaFilters): Promise<QaResponse> {
  const response = await fetch(`${API_BASE_URL}/qa/text`, {
    method: "POST",
    headers: authHeaders(token),
    body: JSON.stringify({ question, filters })
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as QaResponse;
}

export async function getRetrievalTrace(token: string, traceId: string): Promise<RetrievalTrace> {
  const response = await fetch(`${API_BASE_URL}/retrieval-traces/${traceId}`, {
    headers: { Authorization: `Bearer ${token}` }
  });
  if (!response.ok) {
    throw await parseError(response);
  }
  return (await response.json()) as RetrievalTrace;
}
