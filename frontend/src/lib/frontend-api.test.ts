import { describe, expect, it } from "vitest";

import { canMutateLibrary, documentQueryString } from "./frontend-api";

describe("frontend API helpers", () => {
  it("builds document query strings from non-empty filters", () => {
    expect(
      documentQueryString({
        brand: "Kubota",
        model: " ",
        document_type: "manual",
        language: "",
        status: "indexed",
      }),
    ).toBe("?brand=Kubota&document_type=manual&status=indexed");
  });

  it("only allows admin and maintainer roles to mutate the library", () => {
    expect(canMutateLibrary("admin")).toBe(true);
    expect(canMutateLibrary("maintainer")).toBe(true);
    expect(canMutateLibrary("user")).toBe(false);
    expect(canMutateLibrary("evaluator")).toBe(false);
  });
});
