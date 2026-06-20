import { type ApiErrorResponse } from "./errors";

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "";

export type UserRole = "admin" | "maintainer" | "user" | "evaluator";

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
}

export interface CurrentUser {
  username: string;
  role: UserRole;
}

export class ApiRequestError extends Error {
  response: ApiErrorResponse;

  constructor(response: ApiErrorResponse) {
    super(response.error.message);
    this.response = response;
  }
}

async function parseError(response: Response): Promise<ApiRequestError> {
  const payload = (await response.json()) as ApiErrorResponse;
  return new ApiRequestError(payload);
}

export async function login(username: string, password: string): Promise<LoginResponse> {
  const response = await fetch(`${API_BASE_URL}/auth/login`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username, password })
  });

  if (!response.ok) {
    throw await parseError(response);
  }

  return (await response.json()) as LoginResponse;
}

export async function currentUser(token: string): Promise<CurrentUser> {
  const response = await fetch(`${API_BASE_URL}/auth/me`, {
    headers: { Authorization: `Bearer ${token}` }
  });

  if (!response.ok) {
    throw await parseError(response);
  }

  return (await response.json()) as CurrentUser;
}
