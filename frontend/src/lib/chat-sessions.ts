import { useCallback, useMemo, useState } from "react";

import {
  createChatSession,
  deleteChatSession,
  getChatSession,
  listChatSessions,
  updateChatSession,
  type ChatSession,
  type ChatSessionCreateInput,
  type ChatSessionListResponse,
  type ChatSessionUpdateInput,
} from "./frontend-api";

export type { ChatSession, ChatSessionCreateInput, ChatSessionUpdateInput } from "./frontend-api";

export type ChatSessionHistorySource = "remote" | "local";

export type ChatSessionListResult = {
  sessions: ChatSession[];
  total: number;
  source: ChatSessionHistorySource;
  error?: string;
};

export type ChatSessionMutationResult = {
  session: ChatSession;
  source: ChatSessionHistorySource;
  error?: string;
};

export type ChatSessionDeleteResult = {
  session_id: string;
  source: ChatSessionHistorySource;
  error?: string;
};

type ChatSessionRemote = {
  list: (limit?: number) => Promise<ChatSessionListResponse>;
  create: (payload: ChatSessionCreateInput) => Promise<ChatSession>;
  get: (sessionId: string) => Promise<ChatSession>;
  update: (sessionId: string, payload: ChatSessionUpdateInput) => Promise<ChatSession>;
  delete: (sessionId: string) => Promise<{ session_id: string; deleted: boolean }>;
};

const LOCAL_HISTORY_VERSION = 1;
const LOCAL_HISTORY_LIMIT = 50;
const FALLBACK_ERROR = "会话历史暂时无法保存";

export function resolveChatSessionStorage(
  current: Storage | null,
  provided?: Storage,
  getBrowserStorage: () => Storage | null = () => (typeof window === "undefined" ? null : window.localStorage),
): Storage | null {
  if (provided) return provided;
  if (current) return current;
  return getBrowserStorage();
}

export function chatSessionStorageKey(username: string): string {
  return `agromech.chat_sessions.v1.${encodeURIComponent(username)}`;
}

export function loadLocalChatSessions(storage: Storage, username: string): ChatSession[] {
  try {
    const raw = storage.getItem(chatSessionStorageKey(username));
    if (!raw) return [];
    const parsed = JSON.parse(raw) as unknown;
    if (!isRecord(parsed) || parsed.version !== LOCAL_HISTORY_VERSION || parsed.username !== username) {
      return [];
    }
    if (!Array.isArray(parsed.sessions)) {
      return [];
    }
    return parsed.sessions.map(normalizeSession).filter((item): item is ChatSession => item !== null);
  } catch {
    return [];
  }
}

export function saveLocalChatSessions(storage: Storage, username: string, sessions: ChatSession[]): void {
  const orderedSessions = [...sessions]
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at))
    .slice(0, LOCAL_HISTORY_LIMIT);
  storage.setItem(
    chatSessionStorageKey(username),
    JSON.stringify({
      version: LOCAL_HISTORY_VERSION,
      username,
      sessions: orderedSessions,
    }),
  );
}

export function createChatSessionHistoryManager({
  token,
  username,
  storage,
  remote,
}: {
  token: string;
  username: string;
  storage: Storage;
  remote?: ChatSessionRemote;
}) {
  const api = remote ?? defaultRemote(token);

  function readLocal(): ChatSession[] {
    return loadLocalChatSessions(storage, username);
  }

  function writeLocal(sessions: ChatSession[]): void {
    try {
      saveLocalChatSessions(storage, username, sessions);
    } catch {
      // Local history is a fallback; callers still receive the active operation result.
    }
  }

  return {
    async list(limit = LOCAL_HISTORY_LIMIT): Promise<ChatSessionListResult> {
      try {
        const result = await api.list(limit);
        writeLocal(result.items);
        return { sessions: result.items, total: result.total, source: "remote" };
      } catch {
        const sessions = readLocal();
        return { sessions, total: sessions.length, source: "local", error: FALLBACK_ERROR };
      }
    },

    async create(payload: ChatSessionCreateInput): Promise<ChatSessionMutationResult> {
      try {
        const created = await api.create(payload);
        writeLocal(upsertSession(readLocal(), created));
        return { session: created, source: "remote" };
      } catch {
        const created = createLocalSession(payload);
        writeLocal(upsertSession(readLocal(), created));
        return { session: created, source: "local", error: FALLBACK_ERROR };
      }
    },

    async get(sessionId: string): Promise<ChatSessionMutationResult> {
      try {
        const session = await api.get(sessionId);
        writeLocal(upsertSession(readLocal(), session));
        return { session, source: "remote" };
      } catch {
        const session = readLocal().find((item) => item.id === sessionId);
        if (!session) {
          throw new Error("Chat session not found");
        }
        return { session, source: "local", error: FALLBACK_ERROR };
      }
    },

    async update(sessionId: string, payload: ChatSessionUpdateInput): Promise<ChatSessionMutationResult> {
      try {
        const updated = await api.update(sessionId, payload);
        writeLocal(upsertSession(readLocal(), updated));
        return { session: updated, source: "remote" };
      } catch {
        const updated = updateLocalSession(readLocal(), sessionId, payload);
        writeLocal(upsertSession(readLocal(), updated));
        return { session: updated, source: "local", error: FALLBACK_ERROR };
      }
    },

    async delete(sessionId: string): Promise<ChatSessionDeleteResult> {
      try {
        const result = await api.delete(sessionId);
        writeLocal(readLocal().filter((item) => item.id !== sessionId));
        return { session_id: result.session_id, source: "remote" };
      } catch {
        writeLocal(readLocal().filter((item) => item.id !== sessionId));
        return { session_id: sessionId, source: "local", error: FALLBACK_ERROR };
      }
    },
  };
}

export function useChatSessionHistory({
  token,
  username,
  storage,
}: {
  token: string;
  username: string;
  storage?: Storage;
}) {
  const [browserStorage] = useState<Storage | null>(() =>
    resolveChatSessionStorage(null),
  );
  const resolvedStorage = storage ?? browserStorage;
  const manager = useMemo(() => {
    if (!resolvedStorage) return null;
    return createChatSessionHistoryManager({ token, username, storage: resolvedStorage });
  }, [resolvedStorage, token, username]);
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [source, setSource] = useState<ChatSessionHistorySource>("remote");
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  const refresh = useCallback(async () => {
    if (!manager) return;
    setLoading(true);
    try {
      const result = await manager.list();
      setSessions(result.sessions);
      setSource(result.source);
      setError(result.error ?? null);
    } finally {
      setLoading(false);
    }
  }, [manager]);

  const create = useCallback(
    async (payload: ChatSessionCreateInput) => {
      if (!manager) throw new Error("Chat session history is unavailable");
      const result = await manager.create(payload);
      setSessions((current) => upsertSession(current, result.session));
      setSource(result.source);
      setError(result.error ?? null);
      return result.session;
    },
    [manager],
  );

  const update = useCallback(
    async (sessionId: string, payload: ChatSessionUpdateInput) => {
      if (!manager) throw new Error("Chat session history is unavailable");
      const result = await manager.update(sessionId, payload);
      setSessions((current) => upsertSession(current, result.session));
      setSource(result.source);
      setError(result.error ?? null);
      return result.session;
    },
    [manager],
  );

  const remove = useCallback(
    async (sessionId: string) => {
      if (!manager) throw new Error("Chat session history is unavailable");
      const result = await manager.delete(sessionId);
      setSessions((current) => current.filter((session) => session.id !== result.session_id));
      setSource(result.source);
      setError(result.error ?? null);
      return result.session_id;
    },
    [manager],
  );

  return { sessions, source, error, loading, refresh, create, update, remove };
}

function defaultRemote(token: string): ChatSessionRemote {
  return {
    list: (limit) => listChatSessions(token, limit),
    create: (payload) => createChatSession(token, payload),
    get: (sessionId) => getChatSession(token, sessionId),
    update: (sessionId, payload) => updateChatSession(token, sessionId, payload),
    delete: (sessionId) => deleteChatSession(token, sessionId),
  };
}

function createLocalSession(payload: ChatSessionCreateInput): ChatSession {
  const now = new Date().toISOString();
  return {
    id: `local-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    title: payload.title?.trim() || "未命名会话",
    messages: payload.messages ?? [],
    filters: payload.filters ?? {},
    has_image: payload.has_image ?? false,
    created_at: now,
    updated_at: now,
  };
}

function updateLocalSession(sessions: ChatSession[], sessionId: string, payload: ChatSessionUpdateInput): ChatSession {
  const current = sessions.find((session) => session.id === sessionId);
  if (!current) {
    throw new Error("Chat session not found");
  }
  return {
    ...current,
    ...payload,
    title: payload.title?.trim() || current.title,
    updated_at: new Date().toISOString(),
  };
}

function upsertSession(sessions: ChatSession[], session: ChatSession): ChatSession[] {
  return [session, ...sessions.filter((item) => item.id !== session.id)].slice(0, LOCAL_HISTORY_LIMIT);
}

function normalizeSession(value: unknown): ChatSession | null {
  if (!isRecord(value)) return null;
  if (
    typeof value.id !== "string" ||
    typeof value.title !== "string" ||
    !Array.isArray(value.messages) ||
    !isRecord(value.filters) ||
    typeof value.has_image !== "boolean" ||
    typeof value.created_at !== "string" ||
    typeof value.updated_at !== "string"
  ) {
    return null;
  }
  return {
    id: value.id,
    title: value.title,
    messages: value.messages.filter(isRecord),
    filters: value.filters,
    has_image: value.has_image,
    created_at: value.created_at,
    updated_at: value.updated_at,
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
