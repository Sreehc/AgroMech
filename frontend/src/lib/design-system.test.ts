import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readProjectFile(pathFromFrontendRoot: string): string {
  return readFileSync(new URL(`../../${pathFromFrontendRoot}`, import.meta.url), "utf8");
}

describe("design system foundation", () => {
  it("declares Phosphor Icons as the product icon library", () => {
    const packageJson = JSON.parse(readProjectFile("package.json")) as {
      dependencies: Record<string, string>;
    };
    const packageLock = readProjectFile("package-lock.json");

    expect(packageJson.dependencies["@phosphor-icons/react"]).toBeTruthy();
    expect(packageLock).toContain("node_modules/@phosphor-icons/react");
  });

  it("defines light and dark semantic design tokens", () => {
    const globals = readProjectFile("src/app/globals.css");

    expect(globals).toContain("--surface-canvas:");
    expect(globals).toContain("--surface-panel:");
    expect(globals).toContain("--status-info:");
    expect(globals).toContain("--status-success:");
    expect(globals).toContain("--status-warning:");
    expect(globals).toContain("--status-danger:");
    expect(globals).toContain(".dark");
    expect(globals).toContain("--high-contrast-focus:");
  });

  it("keeps the light theme surfaces neutral instead of green-tinted", () => {
    const globals = readProjectFile("src/app/globals.css");

    expect(globals).toContain("--surface-canvas: oklch(0.992 0 0);");
    expect(globals).toContain("--surface-panel: oklch(0.998 0 0);");
    expect(globals).toContain("--surface-inset: oklch(0.965 0 0);");
    expect(globals).toContain("--text-primary: oklch(0.22 0 0);");
    expect(globals).toContain("--border: oklch(0.9 0 0);");
    expect(globals).toContain("--sidebar: oklch(0.985 0 0);");
  });

  it("keeps the dark theme surfaces neutral instead of green-tinted", () => {
    const globals = readProjectFile("src/app/globals.css");

    expect(globals).toContain("--surface-canvas: oklch(0.17 0 0);");
    expect(globals).toContain("--surface-panel: oklch(0.21 0 0);");
    expect(globals).toContain("--surface-raised: oklch(0.25 0 0);");
    expect(globals).toContain("--surface-inset: oklch(0.29 0 0);");
    expect(globals).toContain("--text-primary: oklch(0.96 0 0);");
    expect(globals).toContain("--sidebar: oklch(0.19 0 0);");
    expect(globals).toContain("--sidebar-accent: oklch(0.26 0 0);");
  });
});
