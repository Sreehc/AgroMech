"use client";

import { createContext, useContext } from "react";

import type { useChatSessionHistory } from "./chat-sessions";
import type { Session } from "./session";

export type ShellChatHistory = ReturnType<typeof useChatSessionHistory>;

// AppShell 通过 context 把登录态、会话历史和「当前会话 / 新对话信号」下发给
// 各业务页面（问答、资料库），让 GPT 式侧栏与主区在客户端路由间共享同一份状态。
export type ShellContextValue = {
  session: Session | null;
  hydrated: boolean;
  history: ShellChatHistory;
  activeSessionId: string | null;
  selectSession: (sessionId: string | null) => void;
  // 侧栏点「新对话」时自增，问答页据此清空当前线程。
  newChatSignal: number;
  startNewChat: () => void;
};

export const ShellContext = createContext<ShellContextValue | null>(null);

export function useShell(): ShellContextValue {
  const value = useContext(ShellContext);
  if (!value) {
    throw new Error("useShell must be used within <AppShell>");
  }
  return value;
}
