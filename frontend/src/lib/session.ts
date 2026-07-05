"use client";

import type { UserRole } from "./frontend-api";

export const SESSION_KEY = "agromech.session";
export const RETURN_TO_KEY = "agromech.return_to";
export const SESSION_CHANGE_EVENT = "agromech.session.change";

export type Session = {
  token: string;
  username: string;
  role: UserRole;
};

export function loadSession(): Session | null {
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (!raw) {
    return null;
  }
  try {
    const parsed = JSON.parse(raw) as Session;
    if (!parsed.token || !parsed.username || !parsed.role) {
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

// useSyncExternalStore 要求 getSnapshot 返回稳定引用：只要 localStorage 里的
// 原始字符串没变，就返回缓存的同一对象，否则会触发无限重渲染。
let cachedRaw: string | null = null;
let cachedSession: Session | null = null;

export function loadSessionSnapshot(): Session | null {
  const raw = window.localStorage.getItem(SESSION_KEY);
  if (raw === cachedRaw) {
    return cachedSession;
  }
  cachedRaw = raw;
  cachedSession = loadSession();
  return cachedSession;
}

export function saveSession(session: Session): void {
  window.localStorage.setItem(SESSION_KEY, JSON.stringify(session));
  window.dispatchEvent(new Event(SESSION_CHANGE_EVENT));
}

export function clearSession(): void {
  window.localStorage.removeItem(SESSION_KEY);
  window.dispatchEvent(new Event(SESSION_CHANGE_EVENT));
}

export function saveReturnToPath(path: string): void {
  const normalizedPath = path.trim();
  if (!normalizedPath.startsWith("/")) {
    return;
  }
  if (normalizedPath === "/login") {
    return;
  }
  window.localStorage.setItem(RETURN_TO_KEY, normalizedPath);
}

export function loadReturnToPath(): string | null {
  const raw = window.localStorage.getItem(RETURN_TO_KEY);
  if (!raw) {
    return null;
  }
  const normalizedPath = raw.trim();
  if (!normalizedPath.startsWith("/") || normalizedPath === "/login") {
    return null;
  }
  return normalizedPath;
}

export function clearReturnToPath(): void {
  window.localStorage.removeItem(RETURN_TO_KEY);
}
