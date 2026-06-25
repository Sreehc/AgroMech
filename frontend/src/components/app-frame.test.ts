import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readAppFrame(): string {
  return readFileSync(new URL("./app-frame.tsx", import.meta.url), "utf8");
}

describe("AppFrame navigation shell", () => {
  it("shows brand, primary navigation, user role, sign out, and theme toggle controls", () => {
    const appFrame = readAppFrame();

    expect(appFrame).toContain("AgroMech RAG");
    expect(appFrame).toContain("农机维修资料助手");
    expect(appFrame).toContain("助手问答");
    expect(appFrame).toContain("资料库");
    expect(appFrame).toContain("角色：");
    expect(appFrame).toContain("退出登录");
    expect(appFrame).toContain("切换为深色模式");
    expect(appFrame).toContain("切换为浅色模式");
  });

  it("redirects unauthenticated business page visits to login", () => {
    const appFrame = readAppFrame();

    expect(appFrame).toContain("const [hydrated, setHydrated]");
    expect(appFrame).toContain("setSession(loadSession())");
    expect(appFrame).toContain("saveReturnToPath(pathname)");
    expect(appFrame).toContain('if (hydrated && !session && pathname !== "/login")');
    expect(appFrame).toContain('router.replace("/login")');
    expect(appFrame).toContain("请先登录");
  });

  it("provides a mobile navigation drawer with accessible open and close controls", () => {
    const appFrame = readAppFrame();

    expect(appFrame).toContain("isMobileMenuOpen");
    expect(appFrame).toContain("aria-expanded");
    expect(appFrame).toContain("打开导航菜单");
    expect(appFrame).toContain("关闭导航菜单");
    expect(appFrame).toContain("data-mobile-navigation");
  });

  it("keeps library navigation active for library detail routes", () => {
    const appFrame = readAppFrame();

    expect(appFrame).toContain('pathname.startsWith("/library")');
  });
});
