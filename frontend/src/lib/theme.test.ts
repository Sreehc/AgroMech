import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import {
  nextTheme,
  normalizeTheme,
  resolveInitialTheme,
  themeStorageKey,
} from "./theme";

describe("theme utilities", () => {
  it("normalizes only supported theme values", () => {
    expect(normalizeTheme("dark")).toBe("dark");
    expect(normalizeTheme("light")).toBe("light");
    expect(normalizeTheme("system")).toBeNull();
    expect(normalizeTheme(null)).toBeNull();
  });

  it("resolves initial theme from storage before system preference", () => {
    expect(resolveInitialTheme("dark", false)).toBe("dark");
    expect(resolveInitialTheme("light", true)).toBe("light");
    expect(resolveInitialTheme(null, true)).toBe("dark");
    expect(resolveInitialTheme("invalid", false)).toBe("light");
  });

  it("toggles between light and dark and exposes a stable storage key", () => {
    expect(nextTheme("light")).toBe("dark");
    expect(nextTheme("dark")).toBe("light");
    expect(themeStorageKey).toBe("agromech.theme");
  });

  it("applies dark mode through the root html class and data theme contract", () => {
    const provider = readFileSync(new URL("../components/theme-provider.tsx", import.meta.url), "utf8");

    expect(provider).toContain('document.documentElement.classList.toggle("dark", theme === "dark")');
    expect(provider).toContain("document.documentElement.dataset.theme = theme");
    expect(provider).toContain("safeLocalStorageSet(themeStorageKey, theme)");
  });

  it("wires theme provider through layout and exposes shell theme toggle labels", () => {
    const layout = readFileSync(new URL("../app/layout.tsx", import.meta.url), "utf8");
    const appShell = readFileSync(new URL("../components/app-shell.tsx", import.meta.url), "utf8");

    expect(layout).toContain("ThemeProvider");
    expect(layout).toContain("@/components/theme-provider");
    expect(appShell).toContain("useTheme");
    expect(appShell).toContain("toggleTheme");
    expect(appShell).toContain("切换为深色模式");
    expect(appShell).toContain("切换为浅色模式");
  });
});
