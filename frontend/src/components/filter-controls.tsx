"use client";

import { useId } from "react";

import { Input } from "@/components/ui/input";
import { cn } from "@/lib/utils";
import type { ChatSession, DocumentSummary, DocumentStatus } from "@/lib/frontend-api";

export type SelectOption = {
  value: string;
  label: string;
};

export const statusOptions: SelectOption[] = [
  { value: "", label: "全部状态" },
  { value: "queued", label: "已排队" },
  { value: "processing", label: "处理中" },
  { value: "indexed", label: "入库完成" },
  { value: "failed", label: "处理失败" },
  { value: "reprocessing", label: "重新处理中" },
  { value: "deleting", label: "删除中" },
  { value: "deleted", label: "已删除" },
];

export const languageOptions: SelectOption[] = [
  { value: "", label: "全部语言" },
  { value: "zh-CN", label: "中文（简体）" },
  { value: "en-US", label: "英文（美国）" },
];

export const documentTypeOptions: SelectOption[] = [
  { value: "", label: "全部类型" },
  { value: "manual", label: "说明手册" },
  { value: "repair_manual", label: "维修手册" },
  { value: "fault-code", label: "故障码" },
];

export type FilterOptionCollection = {
  brands: string[];
  allModels: string[];
  modelsByBrand: Record<string, string[]>;
  documentTypes: string[];
  languages: string[];
};

export function collectFilterOptions(documents: DocumentSummary[]): FilterOptionCollection {
  const brands = new Set<string>();
  const allModels = new Set<string>();
  const documentTypes = new Set<string>();
  const languages = new Set<string>();
  const modelsByBrand = new Map<string, Set<string>>();

  for (const document of documents) {
    const brand = document.brand?.trim();
    const model = document.model?.trim();
    const documentType = document.document_type?.trim();
    const language = document.language?.trim();

    if (brand) {
      brands.add(brand);
      if (!modelsByBrand.has(brand)) {
        modelsByBrand.set(brand, new Set<string>());
      }
    }

    if (model) {
      allModels.add(model);
      if (brand) {
        modelsByBrand.get(brand)?.add(model);
      }
    }

    if (documentType) {
      documentTypes.add(documentType);
    }

    if (language) {
      languages.add(language);
    }
  }

  return {
    brands: sortStrings(brands),
    allModels: sortStrings(allModels),
    modelsByBrand: Object.fromEntries(
      [...modelsByBrand.entries()].map(([brand, values]) => [brand, sortStrings(values)]),
    ),
    documentTypes: sortStrings(documentTypes),
    languages: sortStrings(languages),
  };
}

export function collectSessionFilterOptions(sessions: ChatSession[]): FilterOptionCollection {
  const summaries: DocumentSummary[] = sessions.map((session, index) => ({
    id: session.id,
    title: session.title,
    original_file_name: `session-${index}`,
    brand: asString(session.filters.brand),
    model: asString(session.filters.model),
    document_type: asString(session.filters.document_type),
    language: asString(session.filters.language),
    status: "indexed" satisfies DocumentStatus,
    updated_at: session.updated_at,
    summary: null,
    recent_task: null,
    failure: null,
  }));

  return collectFilterOptions(summaries);
}

export function mergeOptionValues(
  baseOptions: SelectOption[],
  dynamicValues: string[],
  labelMap: Record<string, string> = {},
): SelectOption[] {
  const merged = new Map(baseOptions.map((option) => [option.value, option]));
  dynamicValues.forEach((value) => {
    if (!value || merged.has(value)) return;
    merged.set(value, { value, label: labelMap[value] ?? value });
  });
  return [...merged.values()];
}

export function SearchableSelectField({
  label,
  value,
  options,
  placeholder,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  placeholder: string;
  onChange: (value: string) => void;
}) {
  const listId = useId();

  return (
    <label className="grid gap-1 text-sm">
      <span className="text-text-muted">{label}</span>
      <Input
        value={value}
        list={listId}
        placeholder={placeholder}
        onChange={(event) => onChange(event.target.value)}
      />
      <datalist id={listId}>
        {options.map((option) => (
          <option key={option} value={option} />
        ))}
      </datalist>
    </label>
  );
}

export function SelectField({
  label,
  value,
  options,
  onChange,
  className,
}: {
  label: string;
  value: string;
  options: SelectOption[];
  onChange: (value: string) => void;
  className?: string;
}) {
  return (
    <label className={cn("grid gap-1 text-sm", className)}>
      <span className="text-text-muted">{label}</span>
      <select
        className="h-9 rounded-md border border-input bg-surface-raised px-3 py-2 text-foreground outline-none transition focus-visible:border-ring focus-visible:ring-[3px] focus-visible:ring-ring/50"
        value={value}
        onChange={(event) => onChange(event.target.value)}
      >
        {options.map((option) => (
          <option key={`${label}-${option.value || "empty"}`} value={option.value}>
            {option.label}
          </option>
        ))}
      </select>
    </label>
  );
}

function asString(value: unknown): string | null {
  if (typeof value !== "string") return null;
  const trimmed = value.trim();
  return trimmed || null;
}

function sortStrings(values: Iterable<string>): string[] {
  return [...values].sort((left, right) => left.localeCompare(right, "zh-CN"));
}
