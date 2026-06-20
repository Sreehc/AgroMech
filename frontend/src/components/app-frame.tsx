"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

import { clearSession, loadSession, type Session } from "@/lib/session";

export function AppFrame({ children }: { children: ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [session] = useState<Session | null>(() => {
    if (typeof window === "undefined") return null;
    return loadSession();
  });

  useEffect(() => {
    if (!session && pathname !== "/login") {
      router.replace("/login");
    }
  }, [pathname, router, session]);

  if (!session && pathname !== "/login") {
    return <main className="grid min-h-dvh place-items-center bg-[#f4f7f1] text-sm text-[#60704d]">请先登录</main>;
  }

  if (pathname === "/login") {
    return children;
  }

  return (
    <main className="flex min-h-dvh bg-[#f4f7f1] text-[#172016]">
      <aside className="hidden w-72 shrink-0 border-r border-[#d8dfd0] bg-[#edf3e7] px-5 py-6 lg:block">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#60704d]">AgroMech RAG</p>
        <h1 className="mt-3 text-2xl font-semibold leading-tight">农机维修资料助手</h1>
        <nav className="mt-8 grid gap-2 text-sm">
          <Link className={navClass(pathname === "/")} href="/">助手问答</Link>
          <Link className={navClass(pathname === "/library")} href="/library">资料库</Link>
        </nav>
        <div className="mt-8 rounded-lg border border-[#d4dccb] bg-white/70 p-4 text-sm">
          <p className="font-medium text-[#253322]">{session?.username}</p>
          <p className="mt-1 text-[#60704d]">角色：{session?.role}</p>
          <button
            className="mt-4 rounded-md border border-[#cbd6c0] px-3 py-1.5 text-sm hover:bg-white"
            type="button"
            onClick={() => {
              clearSession();
              router.replace("/login");
            }}
          >
            退出登录
          </button>
        </div>
      </aside>
      <section className="flex min-w-0 flex-1 flex-col">
        <header className="flex items-center justify-between border-b border-[#d8dfd0] bg-white/80 px-4 py-3 backdrop-blur md:px-6 lg:hidden">
          <div className="flex gap-3 text-sm">
            <Link href="/">助手</Link>
            <Link href="/library">资料库</Link>
          </div>
          <button
            className="text-sm text-[#60704d]"
            type="button"
            onClick={() => {
              clearSession();
              router.replace("/login");
            }}
          >
            退出
          </button>
        </header>
        {children}
      </section>
    </main>
  );
}

function navClass(active: boolean): string {
  return [
    "rounded-md px-3 py-2 transition",
    active ? "bg-[#253322] text-white" : "text-[#52614a] hover:bg-white/70",
  ].join(" ");
}
