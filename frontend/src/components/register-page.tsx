"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import {
  Books,
  CheckCircle,
  Gauge,
  ShieldCheck,
  Wrench,
} from "@phosphor-icons/react";

import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  ApiRequestError,
  currentUser,
  errorMessage,
  register,
} from "@/lib/frontend-api";
import { clearReturnToPath, loadReturnToPath, saveSession } from "@/lib/session";

const capabilityCards = [
  { title: "可信资料问答", icon: Books },
  { title: "现场图片线索", icon: Gauge },
  { title: "安全提醒优先", icon: ShieldCheck },
];

// 用户名 3-120 字符、密码至少 8 位，与后端 /auth/register 约束保持一致。
const USERNAME_MIN_LENGTH = 3;
const PASSWORD_MIN_LENGTH = 8;

export function RegisterPage() {
  const router = useRouter();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [confirmPassword, setConfirmPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  const trimmedUsername = username.trim();
  const usernameValid = trimmedUsername.length >= USERNAME_MIN_LENGTH;
  const passwordValid = password.length >= PASSWORD_MIN_LENGTH;
  const passwordsMatch = password === confirmPassword;
  const disabled =
    !usernameValid || !passwordValid || !passwordsMatch || submitting;

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (disabled) return;
    setSubmitting(true);
    setError(null);
    try {
      const token = await register(trimmedUsername, password);
      const user = await currentUser(token.access_token);
      saveSession({
        token: token.access_token,
        username: user.username,
        role: user.role,
      });
      const returnTo = loadReturnToPath();
      clearReturnToPath();
      router.replace(returnTo || "/");
    } catch (caught) {
      setError(
        caught instanceof ApiRequestError
          ? errorMessage(caught.response)
          : "服务暂时不可用，请稍后重试。",
      );
      setPassword("");
      setConfirmPassword("");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <main className="min-h-dvh bg-surface-canvas px-4 py-6 text-foreground sm:px-6 lg:px-10">
      <section className="mx-auto grid min-h-[calc(100dvh-3rem)] w-full max-w-6xl items-center gap-10 lg:grid-cols-[1.05fr_0.95fr]">
        {/* Left: product intro, docs-site editorial rhythm */}
        <div className="grid gap-8">
          <div className="flex items-center gap-2.5">
            <span className="grid size-9 place-items-center rounded-lg bg-primary text-primary-foreground">
              <Wrench className="size-5" weight="duotone" />
            </span>
            <span className="text-sm font-semibold uppercase tracking-[0.22em] text-text-muted">
              AgroMech RAG
            </span>
          </div>

          <div>
            <h1 className="max-w-3xl text-4xl font-semibold leading-[1.15] tracking-tight sm:text-5xl">
              农机维修 AI 资料工作台
            </h1>
            <p className="mt-5 max-w-xl text-base leading-7 text-text-muted">
              注册后可保存问答记录、管理个人知识库并上传资料。
            </p>
          </div>

          <div className="flex flex-wrap gap-2.5">
            {capabilityCards.map((card) => {
              const Icon = card.icon;

              return (
                <span
                  className="inline-flex items-center gap-2 rounded-lg border border-border bg-surface-panel px-3 py-2 text-sm font-medium"
                  key={card.title}
                >
                  <Icon className="size-4 text-primary" weight="duotone" />
                  {card.title}
                </span>
              );
            })}
          </div>
        </div>

        {/* Right: sign-up card */}
        <div className="rounded-2xl border border-border bg-surface-raised p-6 shadow-xl shadow-foreground/5 sm:p-8">
          <div className="border-b border-border pb-5">
            <p className="text-xs font-semibold uppercase tracking-[0.18em] text-text-muted">
              Create account
            </p>
            <h2 className="mt-2 text-2xl font-semibold tracking-tight">免费注册</h2>
          </div>

          <form className="mt-6 grid gap-4" onSubmit={submit}>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">账号</span>
              <Input
                autoComplete="username"
                placeholder="至少 3 个字符"
                value={username}
                state={error ? "invalid" : "default"}
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">密码</span>
              <Input
                autoComplete="new-password"
                placeholder="至少 8 位"
                type="password"
                value={password}
                state={error ? "invalid" : "default"}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">确认密码</span>
              <Input
                autoComplete="new-password"
                placeholder="再次输入密码"
                type="password"
                value={confirmPassword}
                state={
                  confirmPassword && !passwordsMatch ? "invalid" : "default"
                }
                onChange={(event) => setConfirmPassword(event.target.value)}
              />
              {confirmPassword && !passwordsMatch ? (
                <span className="text-xs text-status-danger">两次输入的密码不一致。</span>
              ) : null}
            </label>
            {error ? (
              <Alert tone="danger">
                <AlertTitle>注册失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <Button className="h-10 w-full" type="submit" disabled={disabled}>
              {submitting ? "注册中" : "注册"}
            </Button>
          </form>

          <p className="mt-4 text-sm text-text-muted">
            已有账号？
            <Link
              className="ml-1 font-medium text-foreground underline-offset-4 hover:underline"
              href="/login"
            >
              返回登录
            </Link>
          </p>

          <div className="mt-6 rounded-xl border border-border bg-surface-panel p-4">
            <div className="flex items-center justify-between gap-3">
              <div>
                <p className="text-sm font-medium">服务状态</p>
                <p className="mt-1 text-xs text-text-muted">认证服务、资料检索和问答链路</p>
              </div>
              <Badge tone="success">
                <CheckCircle className="size-3.5" weight="fill" />
                可用
              </Badge>
            </div>
          </div>
        </div>
      </section>
    </main>
  );
}
