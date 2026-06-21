"use client";

import { FormEvent, useState } from "react";
import { useRouter } from "next/navigation";
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
import { ApiRequestError, currentUser, errorMessage, login } from "@/lib/frontend-api";
import { saveSession } from "@/lib/session";

const capabilityCards = [
  {
    title: "可信资料问答",
    description: "围绕维修手册、故障码和保养资料给出可追溯回答。",
    icon: Books,
  },
  {
    title: "现场图片线索",
    description: "结合图片观察、OCR 和资料证据辅助排查。",
    icon: Gauge,
  },
  {
    title: "安全提醒优先",
    description: "高风险维修建议保留明确安全提示和不确定性。",
    icon: ShieldCheck,
  },
];

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
    <main className="min-h-dvh bg-surface-canvas px-4 py-6 text-foreground sm:px-6 lg:px-10">
      <section className="mx-auto grid min-h-[calc(100dvh-3rem)] w-full max-w-6xl items-center gap-8 lg:grid-cols-[1.08fr_0.92fr]">
        <div className="grid gap-8">
          <div>
            <Badge tone="info" className="border-primary/30 bg-primary/10 text-primary">
              AgroMech RAG
            </Badge>
            <h1 className="mt-5 max-w-3xl text-4xl font-semibold leading-tight sm:text-5xl">
              农机维修 AI 资料工作台
            </h1>
            <p className="mt-5 max-w-2xl text-base leading-7 text-text-muted">
              面向维修人员、售后服务和资料维护团队，把问答、引用证据、图片线索和资料库状态放进同一个工作流。
            </p>
          </div>

          <div className="grid gap-3 md:grid-cols-3">
            {capabilityCards.map((card) => {
              const Icon = card.icon;

              return (
                <article className="rounded-lg border border-border bg-surface-panel p-4" key={card.title}>
                  <Icon className="size-5 text-primary" weight="duotone" />
                  <h2 className="mt-3 text-sm font-semibold">{card.title}</h2>
                  <p className="mt-2 text-sm leading-6 text-text-muted">{card.description}</p>
                </article>
              );
            })}
          </div>
        </div>

        <div className="rounded-lg border border-border bg-surface-raised p-5 shadow-xl shadow-foreground/5 sm:p-6">
          <div className="flex items-start justify-between gap-4">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.18em] text-text-muted">Secure access</p>
              <h2 className="mt-2 text-2xl font-semibold">登录工作台</h2>
              <p className="mt-2 text-sm text-text-muted">使用系统账号进入问答和资料库。</p>
            </div>
            <Wrench className="size-9 rounded-lg border border-border bg-surface-panel p-2 text-primary" weight="duotone" />
          </div>

          <form className="mt-6 grid gap-4" onSubmit={submit}>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">账号</span>
              <Input
                autoComplete="username"
                placeholder="输入账号"
                value={username}
                state={error ? "invalid" : "default"}
                onChange={(event) => setUsername(event.target.value)}
              />
            </label>
            <label className="grid gap-1.5 text-sm">
              <span className="font-medium">密码</span>
              <Input
                autoComplete="current-password"
                placeholder="输入密码"
                type="password"
                value={password}
                state={error ? "invalid" : "default"}
                onChange={(event) => setPassword(event.target.value)}
              />
            </label>
            {error ? (
              <Alert tone="danger">
                <AlertTitle>登录失败</AlertTitle>
                <AlertDescription>{error}</AlertDescription>
              </Alert>
            ) : null}
            <Button className="h-10 w-full" type="submit" disabled={disabled}>
              {submitting ? "登录中" : "登录"}
            </Button>
          </form>

          <div className="mt-6 rounded-lg border border-border bg-surface-panel p-4">
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
