import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import type { ExportedMessageRepositoryItem } from "@assistant-ui/react";

import {
  clearAnonymousThread,
  createAnonymousHistoryAdapter,
  hasAnonymousThread,
} from "./anonymous-chat-store";

function messageItem(id: string): ExportedMessageRepositoryItem {
  return {
    parentId: null,
    message: {
      id,
      role: "user",
      content: [{ type: "text", text: `message ${id}` }],
      createdAt: new Date(0),
      metadata: { unstable_state: null, unstable_annotations: [], unstable_data: [], steps: [], custom: {} },
      status: { type: "complete", reason: "stop" },
    },
  } as unknown as ExportedMessageRepositoryItem;
}

describe("anonymous chat store", () => {
  beforeEach(() => {
    window.localStorage.clear();
  });

  afterEach(() => {
    window.localStorage.clear();
  });

  it("reports no thread when storage is empty", () => {
    expect(hasAnonymousThread()).toBe(false);
  });

  it("persists appended messages and reports an existing thread", async () => {
    const adapter = createAnonymousHistoryAdapter();
    await adapter.append(messageItem("m-1"));

    expect(hasAnonymousThread()).toBe(true);
    const loaded = await adapter.load();
    expect(loaded.messages).toHaveLength(1);
    expect(loaded.headId).toBe("m-1");
  });

  it("keeps only a single conversation, replacing head on each append", async () => {
    const adapter = createAnonymousHistoryAdapter();
    await adapter.append(messageItem("m-1"));
    await adapter.append(messageItem("m-2"));

    const loaded = await adapter.load();
    expect(loaded.messages.map((item) => item.message.id)).toEqual(["m-1", "m-2"]);
    expect(loaded.headId).toBe("m-2");
  });

  it("clears the stored thread", async () => {
    const adapter = createAnonymousHistoryAdapter();
    await adapter.append(messageItem("m-1"));
    clearAnonymousThread();

    expect(hasAnonymousThread()).toBe(false);
    const loaded = await adapter.load();
    expect(loaded.messages).toHaveLength(0);
  });

  it("survives corrupted storage by resetting to empty", () => {
    window.localStorage.setItem("agromech.anonymous.thread", "not json{");
    expect(hasAnonymousThread()).toBe(false);
  });
});
