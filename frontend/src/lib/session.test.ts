import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readSessionSource(): string {
  return readFileSync(new URL("./session.ts", import.meta.url), "utf8");
}

describe("session helpers", () => {
  it("stores a pending return path for redirect-after-login flow", () => {
    const source = readSessionSource();

    expect(source).toContain('export const RETURN_TO_KEY = "agromech.return_to"');
    expect(source).toContain("export function saveReturnToPath");
    expect(source).toContain("export function loadReturnToPath");
    expect(source).toContain("export function clearReturnToPath");
    expect(source).toContain('export const SESSION_CHANGE_EVENT = "agromech.session.change"');
    expect(source).toContain("window.dispatchEvent(new Event(SESSION_CHANGE_EVENT))");
  });
});
