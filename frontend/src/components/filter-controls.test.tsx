import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import {
  collectFilterOptions,
  documentTypeOptions,
  languageOptions,
  SearchableSelectField,
  statusOptions,
} from "./filter-controls";
import type { DocumentSummary } from "@/lib/frontend-api";

const documents: DocumentSummary[] = [
  {
    id: "doc-1",
    title: "Kubota M7040 Manual",
    original_file_name: "m7040.pdf",
    brand: "Kubota",
    model: "M7040",
    document_type: "manual",
    language: "zh-CN",
    status: "indexed",
    updated_at: "2026-06-20T10:00:00Z",
    summary: null,
    recent_task: null,
    failure: null,
  },
  {
    id: "doc-2",
    title: "Kubota L3901 Fault Codes",
    original_file_name: "l3901.pdf",
    brand: "Kubota",
    model: "L3901",
    document_type: "fault-code",
    language: "en-US",
    status: "failed",
    updated_at: "2026-06-21T10:00:00Z",
    summary: null,
    recent_task: null,
    failure: null,
  },
  {
    id: "doc-3",
    title: "John Deere 6M Guide",
    original_file_name: "6m.pdf",
    brand: "John Deere",
    model: "6M",
    document_type: "repair_manual",
    language: "zh-CN",
    status: "queued",
    updated_at: "2026-06-22T10:00:00Z",
    summary: null,
    recent_task: null,
    failure: null,
  },
];

describe("filter controls", () => {
  it("collects unique brand and brand-scoped model options from documents", () => {
    const options = collectFilterOptions(documents);

    expect(options.brands).toEqual(["John Deere", "Kubota"]);
    expect(options.modelsByBrand["Kubota"]).toEqual(["L3901", "M7040"]);
    expect(options.modelsByBrand["John Deere"]).toEqual(["6M"]);
    expect(options.allModels).toEqual(["6M", "L3901", "M7040"]);
    expect(options.documentTypes).toEqual(["fault-code", "manual", "repair_manual"]);
  });

  it("keeps stable fallback options for status, language, and document type", () => {
    expect(statusOptions.map((option) => option.value)).toContain("indexed");
    expect(languageOptions.map((option) => option.value)).toContain("zh-CN");
    expect(documentTypeOptions.map((option) => option.value)).toContain("manual");
  });

  it("renders searchable select with guidance for freeform fallback", () => {
    const html = renderToStaticMarkup(
      createElement(SearchableSelectField, {
        label: "品牌",
        value: "",
        options: ["Kubota", "John Deere"],
        placeholder: "选择或输入品牌",
        onChange: () => {},
      }),
    );

    expect(html).toContain("品牌");
    expect(html).toContain("选择或输入品牌");
    expect(html).toContain("Kubota");
  });
});
