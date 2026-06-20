import { type UserRole } from "../api/auth";

export const SESSION_KEY = "agromech.session";

export interface Session {
  token: string;
  username: string;
  role: UserRole;
}

export function loadSession(): Session | null {
  const raw = localStorage.getItem(SESSION_KEY);
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

export function saveSession(session: Session): void {
  localStorage.setItem(SESSION_KEY, JSON.stringify(session));
}

export function clearSession(): void {
  localStorage.removeItem(SESSION_KEY);
}

export function canMaintainLibrary(session: Session): boolean {
  return session.role === "admin" || session.role === "maintainer";
}
