"use client";

import {
  Books,
  ChatCircle,
  DotsThree,
  Gear,
  List,
  MagnifyingGlass,
  Moon,
  NotePencil,
  SignOut,
  Sun,
  UserCircle,
  Wrench,
  X,
} from "@phosphor-icons/react";
import { useRouter } from "next/navigation";
import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  useSyncExternalStore,
  type ReactNode,
} from "react";

import { useTheme } from "@/components/theme-provider";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import type { ChatSession } from "@/lib/frontend-api";
import { useChatSessionHistory } from "@/lib/chat-sessions";
import {
  clearAnonymousThread,
  hasAnonymousThread,
} from "@/lib/anonymous-chat-store";
import { ShellContext, type ShellContextValue } from "@/lib/shell-context";
import {
  clearSession,
  loadSessionSnapshot,
  SESSION_CHANGE_EVENT,
  type Session,
} from "@/lib/session";

type ShellView = "chat" | "library";

// AppShell 是 GPT 式全站外壳：左侧单侧栏（新对话 / 搜索对话 / 知识库 / 最近会话 /
// 底部用户区），右侧主区渲染各业务页面。它取代了旧的 AppFrame + AssistantWorkbench
// 双层壳，并通过 ShellContext 把登录态、会话历史、当前会话下发给子页面。
//
// Phase A：仍要求登录才进业务页（沿用 AppFrame 的鉴权水合逻辑）。Phase C 接入
// 匿名对话后，这里会放开未登录访问。
export function AppShell({
  view,
  children,
}: {
  view: ShellView;
  children: ReactNode;
}) {
  const router = useRouter();
  const { theme, toggleTheme } = useTheme();
  const hydrated = useHydrated();
  const session = useSessionSnapshot();

  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [newChatSignal, setNewChatSignal] = useState(0);
  const [searchOpen, setSearchOpen] = useState(false);
  const [userMenuOpen, setUserMenuOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [overwriteConfirmOpen, setOverwriteConfirmOpen] = useState(false);

  const history = useChatSessionHistory({
    token: session?.token ?? "",
    username: session?.username ?? "anonymous",
  });
  const { refresh } = history;

  // 水合完成后再判定登录态，避免静态导出首帧把已登录用户误踢回登录页。
  // 匿名可对话：仅问答页允许未登录访问；知识库等仍要求登录，未登录时重定向。
  useEffect(() => {
    if (!hydrated) return;
    if (!session && view !== "chat") {
      router.replace("/login");
    }
  }, [hydrated, router, session, view]);

  useEffect(() => {
    if (!session) return;
    void refresh();
  }, [refresh, session]);

  const selectSession = useCallback((sessionId: string | null) => {
    setActiveSessionId(sessionId);
  }, []);

  const startNewChat = useCallback(() => {
    setActiveSessionId(null);
    setNewChatSignal((value) => value + 1);
  }, []);

  const shellValue = useMemo<ShellContextValue>(
    () => ({
      session,
      hydrated,
      history,
      activeSessionId,
      selectSession,
      newChatSignal,
      startNewChat,
    }),
    [session, hydrated, history, activeSessionId, selectSession, newChatSignal, startNewChat],
  );

  function goChatNew() {
    // 匿名访客只有一个本地会话：已有对话时先弹确认，避免误覆盖。
    if (!session && hasAnonymousThread()) {
      setOverwriteConfirmOpen(true);
      return;
    }
    startNewChat();
    setMobileNavOpen(false);
    if (view !== "chat") router.push("/");
  }

  function confirmOverwriteNewChat() {
    clearAnonymousThread();
    setOverwriteConfirmOpen(false);
    startNewChat();
    setMobileNavOpen(false);
    if (view !== "chat") router.push("/");
  }

  function goLibrary() {
    setMobileNavOpen(false);
    if (view !== "library") router.push("/library");
  }

  function openSession(sessionId: string) {
    selectSession(sessionId);
    setMobileNavOpen(false);
    if (view !== "chat") router.push("/");
  }

  function handleSignOut() {
    clearSession();
    router.replace("/login");
  }

  // 非问答页仍要求登录：水合未完成前登录态未知，先渲染占位而不重定向；
  // 水合后若仍无 session 再提示（重定向由上面的 effect 处理）。问答页允许匿名，
  // 继续往下渲染完整外壳。
  if (!session && view !== "chat") {
    return (
      <main className="grid min-h-dvh place-items-center bg-surface-canvas text-sm text-text-muted">
        {hydrated ? "请先登录" : null}
      </main>
    );
  }

  const sidebar = (
    <SidebarContent
      view={view}
      sessions={history.sessions}
      activeSessionId={activeSessionId}
      username={session?.username ?? null}
      role={session?.role ?? null}
      theme={theme}
      userMenuOpen={userMenuOpen}
      onToggleUserMenu={() => setUserMenuOpen((open) => !open)}
      onCloseUserMenu={() => setUserMenuOpen(false)}
      onToggleTheme={toggleTheme}
      onSignOut={handleSignOut}
      onLogin={() => router.push("/login")}
      onNewChat={goChatNew}
      onOpenSearch={() => setSearchOpen(true)}
      onOpenLibrary={goLibrary}
      onSelectSession={openSession}
    />
  );

  return (
    <ShellContext.Provider value={shellValue}>
      <div className="flex h-dvh bg-surface-canvas text-foreground">
        {/* 桌面侧栏 */}
        <aside className="hidden w-64 shrink-0 flex-col border-r border-sidebar-border bg-sidebar lg:flex">
          {sidebar}
        </aside>

        {/* 主区 */}
        <section className="flex min-w-0 flex-1 flex-col">
          {/* 移动端顶栏：汉堡按钮打开抽屉 */}
          <header className="flex items-center justify-between border-b border-border bg-surface-panel/90 px-4 py-3 backdrop-blur lg:hidden">
            <button
              aria-label="打开导航菜单"
              aria-expanded={mobileNavOpen}
              aria-controls="shell-mobile-nav"
              className="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-surface-raised text-text-muted transition hover:text-foreground"
              type="button"
              onClick={() => setMobileNavOpen(true)}
            >
              <List className="size-5" />
            </button>
            <span className="text-sm font-semibold">
              {view === "chat" ? "农机维修问答" : "知识库"}
            </span>
            <span className="size-9" />
          </header>

          {children}
        </section>

        {/* 移动端抽屉 */}
        {mobileNavOpen ? (
          <div className="fixed inset-0 z-50 lg:hidden" id="shell-mobile-nav" data-mobile-navigation>
            <button
              aria-label="关闭导航菜单"
              className="absolute inset-0 bg-foreground/35"
              type="button"
              onClick={() => setMobileNavOpen(false)}
            />
            <aside className="absolute left-0 top-0 flex h-full w-[min(18rem,86vw)] flex-col border-r border-border bg-sidebar shadow-2xl">
              {sidebar}
            </aside>
          </div>
        ) : null}

        {/* 搜索对话弹窗 */}
        {searchOpen ? (
          <SearchDialog
            sessions={history.sessions}
            onClose={() => setSearchOpen(false)}
            onNewChat={() => {
              setSearchOpen(false);
              goChatNew();
            }}
            onSelect={(id) => {
              setSearchOpen(false);
              openSession(id);
            }}
          />
        ) : null}

        <ConfirmDialog
          open={overwriteConfirmOpen}
          title="开始新对话？"
          description="未登录状态只保留一个对话，开始新对话会覆盖当前对话内容。登录后可保存多个历史会话。"
          confirmLabel="覆盖并新建"
          cancelLabel="取消"
          destructive
          onOpenChange={setOverwriteConfirmOpen}
          onConfirm={confirmOverwriteNewChat}
        />
      </div>
    </ShellContext.Provider>
  );
}

function SidebarContent({
  view,
  sessions,
  activeSessionId,
  username,
  role,
  theme,
  userMenuOpen,
  onToggleUserMenu,
  onCloseUserMenu,
  onToggleTheme,
  onSignOut,
  onNewChat,
  onOpenSearch,
  onOpenLibrary,
  onSelectSession,
  onLogin,
}: {
  view: ShellView;
  sessions: ChatSession[];
  activeSessionId: string | null;
  username: string | null;
  role: string | null;
  theme: "light" | "dark";
  userMenuOpen: boolean;
  onToggleUserMenu: () => void;
  onCloseUserMenu: () => void;
  onToggleTheme: () => void;
  onSignOut: () => void;
  onNewChat: () => void;
  onOpenSearch: () => void;
  onOpenLibrary: () => void;
  onSelectSession: (sessionId: string) => void;
  onLogin: () => void;
}) {
  return (
    <>
      {/* 顶部：品牌图标 + 主题切换 */}
      <div className="flex items-center justify-between px-3 py-3">
        <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary text-primary-foreground">
          <Wrench className="size-4" weight="duotone" />
        </span>
        <button
          aria-label={theme === "dark" ? "切换为浅色模式" : "切换为深色模式"}
          className="grid size-8 shrink-0 place-items-center rounded-lg text-text-muted transition hover:bg-surface-panel/80 hover:text-foreground"
          type="button"
          onClick={onToggleTheme}
        >
          {theme === "dark" ? <Sun className="size-[1.15rem]" /> : <Moon className="size-[1.15rem]" />}
        </button>
      </div>

      {/* 导航 + 会话列表 */}
      <nav className="mt-1 min-h-0 flex-1 overflow-auto px-2">
        <SidebarNavButton
          active={false}
          icon={<NotePencil className="size-[1.15rem]" weight="duotone" />}
          label="新对话"
          shortcut="⌘⇧O"
          onClick={onNewChat}
        />
        <SidebarNavButton
          active={false}
          icon={<MagnifyingGlass className="size-[1.15rem]" weight="duotone" />}
          label="搜索对话"
          onClick={onOpenSearch}
        />
        <SidebarNavButton
          active={view === "library"}
          icon={<Books className="size-[1.15rem]" weight="duotone" />}
          label="知识库"
          onClick={onOpenLibrary}
        />

        <p className="px-3 pb-1 pt-4 text-xs font-medium text-text-muted">最近</p>
        <div className="grid gap-0.5">
          {sessions.length === 0 ? (
            <p className="px-3 py-2 text-xs text-text-muted">暂无历史会话</p>
          ) : (
            sessions.map((session) => {
              const active = view === "chat" && session.id === activeSessionId;

              return (
                <button
                  className={[
                    "group/session flex items-center justify-between gap-2 rounded-lg px-3 py-2 text-left text-sm transition",
                    active
                      ? "bg-brand-primary/10 text-foreground"
                      : "text-text-muted hover:bg-surface-panel/80 hover:text-foreground",
                  ].join(" ")}
                  key={session.id}
                  type="button"
                  onClick={() => onSelectSession(session.id)}
                >
                  <span className="truncate">{session.title || "未命名会话"}</span>
                  <DotsThree className="size-4 shrink-0 opacity-0 transition group-hover/session:opacity-60" />
                </button>
              );
            })
          )}
        </div>
      </nav>

      {/* 底部用户区：登录后显头像 + 菜单；匿名显引导登录面板。 */}
      {username ? (
        <div className="relative border-t border-sidebar-border p-2">
          {userMenuOpen ? (
            <>
              <button
                aria-label="关闭菜单"
                className="fixed inset-0 z-10 cursor-default"
                type="button"
                onClick={onCloseUserMenu}
              />
              <div className="absolute inset-x-2 bottom-full z-20 mb-1 overflow-hidden rounded-xl border border-border bg-surface-raised py-1 shadow-lg">
                <div className="px-3 py-2 text-xs text-text-muted">角色：{role}</div>
                <button
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-foreground transition hover:bg-surface-panel/80"
                  type="button"
                  onClick={onCloseUserMenu}
                >
                  <Gear className="size-[1.15rem] text-text-muted" weight="duotone" />
                  设置
                </button>
                <button
                  className="flex w-full items-center gap-2.5 px-3 py-2 text-left text-sm text-foreground transition hover:bg-surface-panel/80"
                  type="button"
                  onClick={onSignOut}
                >
                  <SignOut className="size-[1.15rem] text-text-muted" />
                  退出登录
                </button>
              </div>
            </>
          ) : null}
          <button
            className="flex w-full items-center gap-2.5 rounded-lg px-2 py-2 text-left transition hover:bg-surface-panel/80"
            type="button"
            onClick={onToggleUserMenu}
          >
            <UserCircle className="size-7 shrink-0 text-text-muted" weight="duotone" />
            <span className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
              {username}
            </span>
            <DotsThree className="size-4 shrink-0 text-text-muted" />
          </button>
        </div>
      ) : (
        <div className="border-t border-sidebar-border bg-surface-panel/40 p-4">
          <p className="text-sm font-medium text-foreground">保存你的问答记录</p>
          <p className="mt-1 text-xs leading-5 text-text-muted">
            登录后可保存历史会话、管理知识库并上传资料。
          </p>
          <Button
            className="mt-3 w-full justify-center"
            size="sm"
            type="button"
            onClick={onLogin}
          >
            登录
          </Button>
        </div>
      )}
    </>
  );
}

function SidebarNavButton({
  active,
  icon,
  label,
  shortcut,
  onClick,
}: {
  active: boolean;
  icon: ReactNode;
  label: string;
  shortcut?: string;
  onClick: () => void;
}) {
  return (
    <button
      className={[
        "group/nav flex w-full items-center gap-2.5 rounded-lg px-3 py-2 text-left text-sm font-medium transition",
        active
          ? "bg-gradient-to-r from-brand-primary to-brand-accent text-primary-foreground shadow-sm shadow-brand-primary/25"
          : "text-text-muted hover:bg-surface-panel/80 hover:text-foreground",
      ].join(" ")}
      type="button"
      onClick={onClick}
    >
      {icon}
      <span className="flex-1">{label}</span>
      {shortcut ? (
        <span className="text-xs opacity-0 transition group-hover/nav:opacity-60">
          {shortcut}
        </span>
      ) : null}
    </button>
  );
}

function SearchDialog({
  sessions,
  onClose,
  onNewChat,
  onSelect,
}: {
  sessions: ChatSession[];
  onClose: () => void;
  onNewChat: () => void;
  onSelect: (id: string) => void;
}) {
  const [query, setQuery] = useState("");
  const trimmed = query.trim().toLowerCase();
  const matches = trimmed
    ? sessions.filter((s) => (s.title || "").toLowerCase().includes(trimmed))
    : sessions;

  // 按更新时间归类：今天 / 昨天 / 近 7 天 / 更早。
  const groups = groupSessionsByRecency(matches);

  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[12vh]">
      <button
        aria-label="关闭搜索"
        className="absolute inset-0 bg-foreground/40"
        type="button"
        onClick={onClose}
      />
      <div className="relative z-10 w-full max-w-xl overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-2xl">
        <div className="flex items-center gap-2 border-b border-border px-4 py-3">
          <MagnifyingGlass className="size-4 shrink-0 text-text-muted" />
          <input
            autoFocus
            className="min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-text-muted"
            placeholder="搜索对话…"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === "Escape") onClose();
            }}
          />
          <button
            aria-label="关闭"
            className="grid size-7 shrink-0 place-items-center rounded-md text-text-muted transition hover:bg-surface-panel/80 hover:text-foreground"
            type="button"
            onClick={onClose}
          >
            <X className="size-4" />
          </button>
        </div>

        <div className="max-h-[50vh] overflow-auto py-2">
          <button
            className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm text-foreground transition hover:bg-surface-panel/80"
            type="button"
            onClick={onNewChat}
          >
            <NotePencil className="size-[1.15rem] text-text-muted" weight="duotone" />
            新对话
          </button>

          {groups.length === 0 ? (
            <p className="px-4 py-6 text-center text-sm text-text-muted">没有匹配的对话</p>
          ) : (
            groups.map(([label, items]) => (
              <div key={label}>
                <p className="px-4 pb-1 pt-3 text-xs font-medium text-text-muted">{label}</p>
                {items.map((session) => (
                  <button
                    className="flex w-full items-center gap-2.5 px-4 py-2.5 text-left text-sm text-foreground transition hover:bg-surface-panel/80"
                    key={session.id}
                    type="button"
                    onClick={() => onSelect(session.id)}
                  >
                    <ChatCircle className="size-[1.15rem] shrink-0 text-text-muted" />
                    <span className="truncate">{session.title || "未命名会话"}</span>
                  </button>
                ))}
              </div>
            ))
          )}
        </div>
      </div>
    </div>
  );
}

// 按 updated_at 时间戳把会话分到「今天 / 昨天 / 近 7 天 / 更早」。返回有序分组数组。
function groupSessionsByRecency(sessions: ChatSession[]): Array<[string, ChatSession[]]> {
  const now = Date.now();
  const dayMs = 24 * 60 * 60 * 1000;
  const buckets: Record<string, ChatSession[]> = {
    今天: [],
    昨天: [],
    "近 7 天": [],
    更早: [],
  };
  for (const session of sessions) {
    const updated = new Date(session.updated_at).getTime();
    const ageDays = Number.isNaN(updated) ? Infinity : Math.floor((now - updated) / dayMs);
    if (ageDays <= 0) buckets["今天"].push(session);
    else if (ageDays === 1) buckets["昨天"].push(session);
    else if (ageDays <= 7) buckets["近 7 天"].push(session);
    else buckets["更早"].push(session);
  }
  return (Object.entries(buckets) as Array<[string, ChatSession[]]>).filter(
    ([, items]) => items.length > 0,
  );
}

function useSessionSnapshot(): Session | null {
  return useSyncExternalStore(subscribeSession, loadSessionSnapshot, () => null);
}

function useHydrated(): boolean {
  return useSyncExternalStore(
    subscribeNoop,
    () => true,
    () => false,
  );
}

function subscribeNoop(): () => void {
  return () => {};
}

function subscribeSession(onStoreChange: () => void): () => void {
  window.addEventListener(SESSION_CHANGE_EVENT, onStoreChange);
  window.addEventListener("storage", onStoreChange);
  return () => {
    window.removeEventListener(SESSION_CHANGE_EVENT, onStoreChange);
    window.removeEventListener("storage", onStoreChange);
  };
}
