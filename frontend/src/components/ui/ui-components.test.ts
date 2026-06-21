import { existsSync } from "node:fs";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";

import { describe, expect, it } from "vitest";

import { alertVariants } from "./alert";
import { badgeVariants } from "./badge";
import { inputVariants } from "./input";
import { clampProgressValue } from "./progress";
import { StatusBadge } from "./status-badge";
import { statusBadgeVariants } from "./status-badge";

function uiPath(fileName: string): URL {
  return new URL(`./${fileName}`, import.meta.url);
}

describe("base ui components", () => {
  it("provides the required component files", () => {
    for (const fileName of [
      "input.tsx",
      "badge.tsx",
      "alert.tsx",
      "empty-state.tsx",
      "confirm-dialog.tsx",
      "page-header.tsx",
      "status-badge.tsx",
      "progress.tsx",
    ]) {
      expect(existsSync(uiPath(fileName))).toBe(true);
    }
  });

  it("uses semantic tokens for dark-mode-ready input styling", () => {
    expect(inputVariants()).toContain("border-input");
    expect(inputVariants()).toContain("bg-surface-raised");
    expect(inputVariants()).toContain("text-foreground");
  });

  it("covers alert, badge, and status badge tone variants", () => {
    expect(badgeVariants({ tone: "success" })).toContain("text-status-success");
    expect(badgeVariants({ tone: "danger" })).toContain("text-status-danger");
    expect(alertVariants({ tone: "warning" })).toContain("text-status-warning");
    expect(statusBadgeVariants({ tone: "info" })).toContain("text-status-info");
  });

  it("renders known and unknown document status badges with stable labels", () => {
    const indexedHtml = renderToStaticMarkup(createElement(StatusBadge, { status: "indexed" }));
    const unknownHtml = renderToStaticMarkup(createElement(StatusBadge, { status: "archived" }));

    expect(indexedHtml).toContain("已索引");
    expect(unknownHtml).toContain("未知状态");
    expect(unknownHtml).toContain("未知后端状态：archived");
  });

  it("clamps progress values to valid percentages", () => {
    expect(clampProgressValue(-10)).toBe(0);
    expect(clampProgressValue(42)).toBe(42);
    expect(clampProgressValue(140)).toBe(100);
  });
});
