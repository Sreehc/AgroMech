"use client";

import {
  Books,
  ChatCircleText,
  List,
  Moon,
  SignOut,
  Sun,
  UserCircle,
  X,
} from "@phosphor-icons/react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ComponentType, type ReactNode } from "react";

import { useTheme } from "@/components/theme-provider";
import { clearSession, loadSession, type Session } from "@/lib/session";

type NavItem = {
  href: string;
  label: string;
  shortLabel: string;
  description: string;
  icon: ComponentType<{ className?: string; weight?: "duotone" | "regular" }>;
};

const navItems: NavItem[] = [
  {
    href: "/",
    label: "助手问答",
    shortLabel: "问答",
    description: "维修问答与证据检索",
    icon: ChatCircleText,
  },
  {
    href: "/library",
    label: "资料库",
    shortLabel: "资料",
    description: "资料管理与处理状态",
    icon: Books,
  },
];

export function AppFrame({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const { theme, toggleTheme } = useTheme();
  const [isMobileMenuOpen, setIsMobileMenuOpen] = useState(false);
  const [session, setSession] = useState<Session | null>(null);
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    setSession(loadSession());
    setHydrated(true);
  }, []);

  useEffect(() => {
    if (hydrated && !session && pathname !== "/login") {
      router.replace("/login");
    }
  }, [hydrated, pathname, router, session]);

  if (!hydrated && pathname !== "/login") {
    return <main className="grid min-h-dvh place-items-center bg-surface-canvas text-sm text-text-muted">正在加载</main>;
  }

  if (hydrated && !session && pathname !== "/login") {
    return <main className="grid min-h-dvh place-items-center bg-surface-canvas text-sm text-text-muted">请先登录</main>;
  }

  if (pathname === "/login") {
    return children;
  }

  const activeTitle = navItems.find((item) => isNavActive(pathname, item.href))?.label ?? "AgroMech";

  function handleSignOut() {
    clearSession();
    setSession(null);
    router.replace("/login");
  }

  return (
    <main className="flex min-h-dvh bg-surface-canvas text-foreground">
      <aside className="hidden w-72 shrink-0 border-r border-sidebar-border bg-sidebar px-5 py-6 lg:flex lg:flex-col">
        <ShellBrand />
        <ShellNavigation pathname={pathname} className="mt-8" />
        <div className="mt-auto">
          <UserPanel
            session={session}
            theme={theme}
            onToggleTheme={toggleTheme}
            onSignOut={handleSignOut}
          />
        </div>
      </aside>

      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-border bg-surface-panel/90 px-4 py-3 backdrop-blur md:px-6 lg:hidden">
          <div className="min-w-0">
            <p className="text-[0.68rem] font-semibold uppercase tracking-[0.16em] text-text-muted">AgroMech</p>
            <h1 className="truncate text-base font-semibold">{activeTitle}</h1>
          </div>
          <div className="flex items-center gap-2">
            <button
              className="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-surface-raised text-text-muted transition hover:text-foreground"
              type="button"
              aria-label={theme === "dark" ? "切换为浅色模式" : "切换为深色模式"}
              onClick={toggleTheme}
            >
              {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
            </button>
            <button
              className="inline-flex size-9 items-center justify-center rounded-lg border border-border bg-surface-raised text-text-muted transition hover:text-foreground"
              type="button"
              aria-label="打开导航菜单"
              aria-controls="mobile-navigation"
              aria-expanded={isMobileMenuOpen}
              onClick={() => setIsMobileMenuOpen(true)}
            >
              <List className="size-5" />
            </button>
          </div>
        </header>

        {isMobileMenuOpen ? (
          <div className="fixed inset-0 z-50 lg:hidden" data-mobile-navigation>
            <button
              className="absolute inset-0 bg-foreground/35"
              type="button"
              aria-label="关闭导航菜单"
              onClick={() => setIsMobileMenuOpen(false)}
            />
            <aside
              id="mobile-navigation"
              className="absolute right-0 top-0 flex h-full w-[min(22rem,88vw)] flex-col border-l border-border bg-surface-panel p-5 shadow-2xl"
            >
              <div className="flex items-start justify-between gap-3">
                <ShellBrand compact />
                <button
                  className="inline-flex size-8 items-center justify-center rounded-lg border border-border bg-surface-raised text-text-muted transition hover:text-foreground"
                  type="button"
                  aria-label="关闭导航菜单"
                  onClick={() => setIsMobileMenuOpen(false)}
                >
                  <X className="size-4" />
                </button>
              </div>
              <ShellNavigation
                pathname={pathname}
                className="mt-7"
                compact
                onNavigate={() => setIsMobileMenuOpen(false)}
              />
              <div className="mt-auto">
                <UserPanel
                  session={session}
                  theme={theme}
                  onToggleTheme={toggleTheme}
                  onSignOut={handleSignOut}
                  compact
                />
              </div>
            </aside>
          </div>
        ) : null}

        {children}
      </section>
    </main>
  );
}

function ShellBrand({ compact = false }: { compact?: boolean }) {
  return (
    <div>
      <p className="text-xs font-semibold uppercase tracking-[0.18em] text-text-muted">AgroMech RAG</p>
      <h1 className={compact ? "mt-2 text-xl font-semibold leading-tight" : "mt-3 text-2xl font-semibold leading-tight"}>
        农机维修资料助手
      </h1>
      {!compact ? <p className="mt-2 text-sm text-text-muted">围绕维修资料、图片线索和证据来源工作</p> : null}
    </div>
  );
}

function ShellNavigation({
  pathname,
  className,
  compact = false,
  onNavigate,
}: {
  pathname: string;
  className?: string;
  compact?: boolean;
  onNavigate?: () => void;
}) {
  return (
    <nav className={["grid gap-2 text-sm", className].filter(Boolean).join(" ")} aria-label="主导航">
      {navItems.map((item) => {
        const Icon = item.icon;
        const active = isNavActive(pathname, item.href);

        return (
          <Link className={navClass(active)} href={item.href} key={item.href} onClick={onNavigate}>
            <Icon className="size-5" weight="duotone" />
            <span className="min-w-0">
              <span className="block font-medium">{compact ? item.shortLabel : item.label}</span>
              {!compact ? <span className="block text-xs opacity-75">{item.description}</span> : null}
            </span>
          </Link>
        );
      })}
    </nav>
  );
}

function UserPanel({
  session,
  theme,
  onToggleTheme,
  onSignOut,
  compact = false,
}: {
  session: Session | null;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  onSignOut: () => void;
  compact?: boolean;
}) {
  return (
    <section className="rounded-lg border border-sidebar-border bg-surface-panel/80 p-4 text-sm">
      <p className="flex items-center gap-2 font-medium text-foreground">
        <UserCircle className="size-5 text-text-muted" weight="duotone" />
        <span className="min-w-0 truncate">{session?.username}</span>
      </p>
      <p className="mt-1 text-text-muted">角色：{session?.role}</p>
      <div className={compact ? "mt-4 grid grid-cols-2 gap-2" : "mt-4 grid gap-2"}>
        <button
          className="inline-flex items-center justify-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm transition hover:bg-surface-raised"
          type="button"
          aria-label={theme === "dark" ? "切换为浅色模式" : "切换为深色模式"}
          onClick={onToggleTheme}
        >
          {theme === "dark" ? <Sun className="size-4" /> : <Moon className="size-4" />}
          {theme === "dark" ? "浅色模式" : "深色模式"}
        </button>
        <button
          className="inline-flex items-center justify-center gap-2 rounded-md border border-border px-3 py-1.5 text-sm transition hover:bg-surface-raised"
          type="button"
          onClick={onSignOut}
        >
          <SignOut className="size-4" />
          退出登录
        </button>
      </div>
    </section>
  );
}

function isNavActive(pathname: string, href: string): boolean {
  if (href === "/library") {
    return pathname.startsWith("/library");
  }
  return pathname === href;
}

function navClass(active: boolean): string {
  return [
    "inline-flex items-center gap-3 rounded-lg px-3 py-2.5 transition",
    active ? "bg-primary text-primary-foreground shadow-sm" : "text-text-muted hover:bg-surface-panel/80 hover:text-foreground",
  ].join(" ");
}
