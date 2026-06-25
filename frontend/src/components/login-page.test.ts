import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readLoginPage(): string {
  return readFileSync(new URL("./login-page.tsx", import.meta.url), "utf8");
}

describe("LoginPage productized shell", () => {
  it("presents AgroMech as a maintenance knowledge workspace with service status", () => {
    const loginPage = readLoginPage();

    expect(loginPage).toContain("农机维修 AI 资料工作台");
    expect(loginPage).toContain("可信资料问答");
    expect(loginPage).toContain("服务状态");
    expect(loginPage).toContain("认证服务");
  });

  it("uses shared UI primitives and preserves required login states", () => {
    const loginPage = readLoginPage();

    expect(loginPage).toContain("@/components/ui/input");
    expect(loginPage).toContain("@/components/ui/button");
    expect(loginPage).toContain("@/components/ui/alert");
    expect(loginPage).toContain("@/components/ui/badge");
    expect(loginPage).toContain('username.trim() === "" || password === "" || submitting');
    expect(loginPage).toContain("disabled={disabled}");
    expect(loginPage).toContain("登录中");
    expect(loginPage).toContain('setPassword("")');
    expect(loginPage).not.toContain('setUsername("")');
    expect(loginPage).toContain("loadReturnToPath");
    expect(loginPage).toContain("clearReturnToPath");
    expect(loginPage).toContain("router.replace(returnTo || \"/\")");
  });

  it("uses semantic surface and text tokens for light and dark readability", () => {
    const loginPage = readLoginPage();

    expect(loginPage).toContain("bg-surface-canvas");
    expect(loginPage).toContain("bg-surface-raised");
    expect(loginPage).toContain("bg-surface-panel");
    expect(loginPage).toContain("text-foreground");
    expect(loginPage).toContain("text-text-muted");
  });
});
