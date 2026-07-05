"use client";

import { NotePencil, X } from "@phosphor-icons/react";
import { useState } from "react";

import { Assistant } from "@/app/assistant";
import { EvidencePanel } from "@/components/evidence-panel";
import type {
  AgroMechContextFilters,
  AgroMechEvidenceSelection,
} from "@/lib/agromech-chat";
import { useShell } from "@/lib/shell-context";

// 问答页主区：GPT 式布局下的对话区。用真实 assistant-ui 流式 runtime（Assistant）
// 渲染消息与 composer，空状态由 Thread 自带的居中欢迎 + 建议词承担。顶栏显示当前
// 会话标题（可重命名），点击回答引用时右侧滑出证据面板。
export function ChatPage() {
  const { activeSessionId, history, newChatSignal } = useShell();

  const activeSession = activeSessionId
    ? history.sessions.find((item) => item.id === activeSessionId) ?? null
    : null;

  async function renameActiveSession(title: string) {
    if (!activeSessionId) return;
    const trimmed = title.trim();
    if (!trimmed) return;
    try {
      await history.update(activeSessionId, { title: trimmed });
    } catch {
      // 重命名失败时保持原标题；history 内部已做本地兜底。
    }
  }

  return (
    <div className="relative flex min-h-0 flex-1 flex-col">
      <header className="flex items-center justify-between gap-3 px-4 py-3 md:px-6">
        {activeSession ? (
          <ChatTitle
            key={activeSession.id}
            title={activeSession.title || "未命名会话"}
            onRename={renameActiveSession}
          />
        ) : (
          <p className="truncate text-sm font-medium text-text-muted">新对话</p>
        )}
      </header>

      {/* 会话线程 + 证据面板都随「当前会话 / 新对话信号」重挂载，切换会话时
          证据选择自然重置，无需 effect 清理。 */}
      <ChatConversation
        key={activeSessionId ?? `new-${newChatSignal}`}
        sessionId={activeSessionId}
      />
    </div>
  );
}

function ChatConversation({ sessionId }: { sessionId: string | null }) {
  const { session } = useShell();
  const [filters] = useState<AgroMechContextFilters>({});
  const [selectedEvidence, setSelectedEvidence] =
    useState<AgroMechEvidenceSelection | null>(null);

  return (
    <>
      <div className="min-h-0 flex-1">
        <Assistant
          sessionId={sessionId ?? undefined}
          token={session?.token}
          filters={filters}
          onCitationSelect={setSelectedEvidence}
        />
      </div>

      {selectedEvidence ? (
        <div className="fixed inset-0 z-50">
          <button
            aria-label="关闭证据面板"
            className="absolute inset-0 bg-foreground/35"
            type="button"
            onClick={() => setSelectedEvidence(null)}
          />
          <aside className="absolute right-0 top-0 flex h-full w-[min(30rem,94vw)] flex-col overflow-y-auto border-l border-border bg-surface-panel p-4 shadow-2xl md:p-5">
            <div className="mb-3 flex items-center justify-end">
              <button
                aria-label="关闭证据面板"
                className="grid size-7 place-items-center rounded-md text-text-muted transition hover:bg-surface-raised hover:text-foreground"
                type="button"
                onClick={() => setSelectedEvidence(null)}
              >
                <X className="size-4" />
              </button>
            </div>
            <EvidencePanel
              payload={selectedEvidence.payload}
              activeIndex={selectedEvidence.citationIndex}
              previewToken={session?.token}
              onSelect={(citationIndex) =>
                setSelectedEvidence((current) =>
                  current ? { ...current, citationIndex } : current,
                )
              }
              onClose={() => setSelectedEvidence(null)}
            />
          </aside>
        </div>
      ) : null}
    </>
  );
}

// 会话标题：点铅笔或标题进入编辑，回车/失焦保存，Esc 取消。
function ChatTitle({
  title,
  onRename,
}: {
  title: string;
  onRename: (title: string) => void;
}) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(title);

  if (editing) {
    return (
      <input
        autoFocus
        className="min-w-0 flex-1 rounded-md border border-border bg-surface-raised px-2 py-1 text-sm font-medium outline-none focus:border-brand-primary/50"
        value={value}
        onChange={(event) => setValue(event.target.value)}
        onBlur={() => {
          setEditing(false);
          onRename(value);
        }}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            setEditing(false);
            onRename(value);
          }
          if (event.key === "Escape") {
            setValue(title);
            setEditing(false);
          }
        }}
      />
    );
  }

  return (
    <button
      className="group/title flex min-w-0 items-center gap-1.5 rounded-md px-1 py-1 text-left transition hover:bg-surface-panel/60"
      title="重命名会话"
      type="button"
      onClick={() => {
        setValue(title);
        setEditing(true);
      }}
    >
      <span className="truncate text-sm font-medium text-foreground">{title}</span>
      <NotePencil className="size-3.5 shrink-0 text-text-muted opacity-0 transition group-hover/title:opacity-100" />
    </button>
  );
}
