"use client";

import type {
  ExportedMessageRepositoryItem,
  ThreadHistoryAdapter,
} from "@assistant-ui/react";

// 匿名访客只保留「一个」对话，整段存 localStorage（对齐产品决策：未登录可对话，
// 但仅单个会话，新开对话会覆盖旧的）。登录用户走后端会话历史，不用这里。
const ANONYMOUS_THREAD_KEY = "agromech.anonymous.thread";
export const ANONYMOUS_THREAD_CHANGE_EVENT = "agromech.anonymous.thread.change";

type StoredThread = {
  headId: string | null;
  items: unknown[];
};

function readStored(): StoredThread {
  if (typeof window === "undefined") {
    return { headId: null, items: [] };
  }
  try {
    const raw = window.localStorage.getItem(ANONYMOUS_THREAD_KEY);
    if (!raw) {
      return { headId: null, items: [] };
    }
    const parsed = JSON.parse(raw) as StoredThread;
    if (!Array.isArray(parsed.items)) {
      return { headId: null, items: [] };
    }
    return { headId: parsed.headId ?? null, items: parsed.items };
  } catch {
    return { headId: null, items: [] };
  }
}

function writeStored(thread: StoredThread): void {
  if (typeof window === "undefined") return;
  try {
    window.localStorage.setItem(ANONYMOUS_THREAD_KEY, JSON.stringify(thread));
    window.dispatchEvent(new Event(ANONYMOUS_THREAD_CHANGE_EVENT));
  } catch {
    // localStorage 不可用时静默降级为纯内存会话，不影响当前对话。
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function storedItemId(item: unknown): string | null {
  if (!isRecord(item)) return null;
  if (typeof item.id === "string") return item.id;
  const message = item.message;
  if (isRecord(message) && typeof message.id === "string") return message.id;
  return null;
}

// 是否已有匿名对话（用于「新对话」时判断是否需要弹覆盖确认）。
export function hasAnonymousThread(): boolean {
  return readStored().items.length > 0;
}

// 清空匿名对话（新对话覆盖、或登录后迁移完毕时调用）。
export function clearAnonymousThread(): void {
  writeStored({ headId: null, items: [] });
}

// 构造一个把整段对话读写 localStorage 的 ThreadHistoryAdapter。
export function createAnonymousHistoryAdapter(): ThreadHistoryAdapter {
  return {
    async load() {
      const stored = readStored();
      return { headId: stored.headId, messages: stored.items as ExportedMessageRepositoryItem[] };
    },
    async append(item: ExportedMessageRepositoryItem) {
      const stored = readStored();
      const items = stored.items.filter(
        (existing) => storedItemId(existing) !== item.message.id,
      );
      items.push(item);
      writeStored({ headId: item.message.id, items });
    },
    async delete(items: ExportedMessageRepositoryItem[]) {
      const removeIds = new Set(items.map((item) => item.message.id));
      const stored = readStored();
      const remaining = stored.items.filter(
        (existing) => {
          const id = storedItemId(existing);
          return id === null || !removeIds.has(id);
        },
      );
      const headId = remaining.length ? storedItemId(remaining[remaining.length - 1]) : null;
      writeStored({ headId, items: remaining });
    },
    withFormat(formatAdapter) {
      return {
        async load() {
          const stored = readStored();
          return {
            headId: stored.headId,
            messages: stored.items.flatMap((item) => {
              if (!isRecord(item) || item.format !== formatAdapter.format) return [];
              return [formatAdapter.decode(item as never)];
            }),
          };
        },
        async append(item) {
          const stored = readStored();
          const id = formatAdapter.getId(item.message);
          const entry = {
            id,
            parent_id: item.parentId,
            format: formatAdapter.format,
            content: formatAdapter.encode(item),
          };
          const items = stored.items.filter((existing) => storedItemId(existing) !== id);
          items.push(entry);
          writeStored({ headId: id, items });
        },
        async delete(items) {
          const removeIds = new Set(items.map((item) => formatAdapter.getId(item.message)));
          const stored = readStored();
          const remaining = stored.items.filter((existing) => {
            const id = storedItemId(existing);
            return id === null || !removeIds.has(id);
          });
          const headId = remaining.length ? storedItemId(remaining[remaining.length - 1]) : null;
          writeStored({ headId, items: remaining });
        },
      };
    },
  };
}
