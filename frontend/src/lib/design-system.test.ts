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
});
