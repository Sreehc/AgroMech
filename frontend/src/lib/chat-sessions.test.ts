import { describe, expect, it } from "vitest";

import {
  chatSessionStorageKey,
  createChatSessionHistoryManager,
  loadLocalChatSessions,
  saveLocalChatSessions,
  type ChatSession,
} from "./chat-sessions";

function session(overrides: Partial<ChatSession> = {}): ChatSession {
  return {
    id: "session-a",
    title: "液压提升无力",
    messages: [{ role: "user", content: "怎么排查" }],
    filters: { brand: "Kubota" },
    has_image: false,
    created_at: "2026-06-20T10:00:00Z",
    updated_at: "2026-06-20T10:01:00Z",
    ...overrides,
  };
}

function storage(initial: Record<string, string> = {}): Storage {
  const data = new Map(Object.entries(initial));
  return {
    get length() {
      return data.size;
    },
    clear() {
      data.clear();
    },
    getItem(key: string) {
      return data.get(key) ?? null;
    },
    key(index: number) {
      return Array.from(data.keys())[index] ?? null;
    },
    removeItem(key: string) {
      data.delete(key);
    },
    setItem(key: string, value: string) {
      data.set(key, value);
    },
  };
}

describe("chat session history", () => {
  it("stores local fallback sessions per username and ignores other users", () => {
    const localStorage = storage();

    saveLocalChatSessions(localStorage, "alice", [session({ id: "alice-session" })]);
    saveLocalChatSessions(localStorage, "bob", [session({ id: "bob-session" })]);

    expect(chatSessionStorageKey("alice")).not.toBe(chatSessionStorageKey("bob"));
    expect(loadLocalChatSessions(localStorage, "alice")).toEqual([session({ id: "alice-session" })]);
    expect(loadLocalChatSessions(localStorage, "bob")).toEqual([session({ id: "bob-session" })]);
  });

  it("drops damaged or mismatched local history without throwing", () => {
    const localStorage = storage({
      [chatSessionStorageKey("alice")]: "{bad json",
      [chatSessionStorageKey("bob")]: JSON.stringify({
        version: 1,
        username: "mallory",
        sessions: [session()],
      }),
    });

    expect(loadLocalChatSessions(localStorage, "alice")).toEqual([]);
    expect(loadLocalChatSessions(localStorage, "bob")).toEqual([]);
  });

  it("falls back to local history when backend listing fails and preserves changes locally when saves fail", async () => {
    const localStorage = storage();
    const fallbackSession = session({ id: "local-session", title: "本地会话" });
    saveLocalChatSessions(localStorage, "alice", [fallbackSession]);

    const manager = createChatSessionHistoryManager({
      token: "token-a",
      username: "alice",
      storage: localStorage,
      remote: {
        list: async () => {
          throw new Error("backend unavailable");
        },
        create: async () => {
          throw new Error("create unavailable");
        },
        get: async () => fallbackSession,
        update: async () => {
          throw new Error("update unavailable");
        },
        delete: async () => {
          throw new Error("delete unavailable");
        },
      },
    });

    const listed = await manager.list();
    expect(listed.sessions).toEqual([fallbackSession]);
    expect(listed.source).toBe("local");
    expect(listed.error).toBe("会话历史暂时无法保存");

    const created = await manager.create({
      title: "新建会话",
      messages: [],
      filters: { model: "M7040" },
      has_image: false,
    });
    expect(created.source).toBe("local");
    expect(loadLocalChatSessions(localStorage, "alice")[0].title).toBe("新建会话");

    const updated = await manager.update(created.session.id, {
      title: "更新后的会话",
      messages: [{ role: "user", content: "继续排查" }],
    });
    expect(updated.source).toBe("local");
    expect(loadLocalChatSessions(localStorage, "alice")[0].title).toBe("更新后的会话");

    const removed = await manager.delete(created.session.id);
    expect(removed.source).toBe("local");
    expect(loadLocalChatSessions(localStorage, "alice").some((item) => item.id === created.session.id)).toBe(false);
  });

  it("restores remote history and mirrors it into the local fallback cache", async () => {
    const localStorage = storage();
    const remoteSession = session({
      id: "remote-session",
      title: "远端会话",
      filters: { brand: "Kubota", model: "M7040" },
      has_image: true,
    });

    const manager = createChatSessionHistoryManager({
      token: "token-a",
      username: "alice",
      storage: localStorage,
      remote: {
        list: async (limit = 50) => ({ total: 1, items: limit > 0 ? [remoteSession] : [] }),
        create: async () => remoteSession,
        get: async () => remoteSession,
        update: async () => remoteSession,
        delete: async () => ({ session_id: remoteSession.id, deleted: true }),
      },
    });

    const listed = await manager.list();
    const fetched = await manager.get(remoteSession.id);

    expect(listed).toEqual({ sessions: [remoteSession], total: 1, source: "remote" });
    expect(fetched).toEqual({ session: remoteSession, source: "remote" });
    expect(loadLocalChatSessions(localStorage, "alice")).toEqual([remoteSession]);
  });
});
