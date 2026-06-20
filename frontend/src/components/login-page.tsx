"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";

import { ApiRequestError, currentUser, errorMessage, login } from "@/lib/frontend-api";
import { saveSession } from "@/lib/session";

export function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const disabled = username.trim() === "" || password === "" || submitting;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    setSubmitting(true);
    setError(null);
    try {
      const token = await login(username.trim(), password);
      const user = await currentUser(token.access_token);
      saveSession({ token: token.access_token, username: user.username, role: user.role });
      router.replace("/");
    } catch (caught) {
      setError(caught instanceof ApiRequestError ? errorMessage(caught.response) : "服务暂时不可用，请稍后重试。");
      setPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="grid min-h-dvh place-items-center bg-[#edf3e7] px-4 text-[#172016]">
      <section className="w-full max-w-sm rounded-lg border border-[#d4dccb] bg-white p-6 shadow-sm">
        <p className="text-xs font-semibold uppercase tracking-[0.18em] text-[#60704d]">AgroMech RAG</p>
        <h1 className="mt-3 text-2xl font-semibold">登录</h1>
        <form className="mt-6 grid gap-4" onSubmit={submit}>
          <label className="grid gap-1.5 text-sm">
            <span>账号</span>
            <input className="rounded-md border border-[#cbd6c0] px-3 py-2" autoComplete="username" value={username} onChange={(event) => setUsername(event.target.value)} />
          </label>
          <label className="grid gap-1.5 text-sm">
            <span>密码</span>
            <input className="rounded-md border border-[#cbd6c0] px-3 py-2" autoComplete="current-password" type="password" value={password} onChange={(event) => setPassword(event.target.value)} />
          </label>
          {error ? <p className="rounded-md bg-red-50 px-3 py-2 text-sm text-red-700" role="alert">{error}</p> : null}
          <button className="rounded-md bg-[#253322] px-4 py-2 text-white disabled:cursor-not-allowed disabled:opacity-50" type="submit" disabled={disabled}>
            {submitting ? "登录中" : "登录"}
          </button>
        </form>
      </section>
    </main>
  );
}
