"use client";

import {
  ChatCircleText,
  ClockCounterClockwise,
  Database,
  Plus,
  WarningCircle,
} from "@phosphor-icons/react";
import { useEffect, useState, type ReactNode } from "react";

import { EvidencePanel } from "@/components/evidence-panel";
import {
  collectSessionFilterOptions,
  documentTypeOptions,
  languageOptions,
  mergeOptionValues,
  SearchableSelectField,
  SelectField,
} from "@/components/filter-controls";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { EmptyState } from "@/components/ui/empty-state";
import type { AgroMechContextFilters, AgroMechEvidenceSelection } from "@/lib/agromech-chat";
import { useChatSessionHistory } from "@/lib/chat-sessions";
import { loadSession, type Session } from "@/lib/session";

const contextFilterFields = [
  { key: "brand", label: "品牌" },
  { key: "model", label: "型号" },
  { key: "document_type", label: "资料类型" },
  { key: "language", label: "语言" },
] as const;

export function AssistantWorkbench({
  activeSessionId,
  onActiveSessionChange,
  activeFilters,
  onActiveFiltersChange,
  selectedEvidence,
  onEvidenceSelect,
  onEvidenceClose,
  children,
}: {
  activeSessionId: string | null;
  onActiveSessionChange: (sessionId: string) => void;
  activeFilters: AgroMechContextFilters;
  onActiveFiltersChange: (filters: AgroMechContextFilters) => void;
  selectedEvidence: AgroMechEvidenceSelection | null;
  onEvidenceSelect: (citationIndex: number) => void;
  onEvidenceClose: () => void;
  children: ReactNode;
}) {
  const [session] = useState<Session | null>(() => {
    if (typeof window === "undefined") return null;
    return loadSession();
  });
  const [actionError, setActionError] = useState<string | null>(null);
  const history = useChatSessionHistory({
    token: session?.token ?? "",
    username: session?.username ?? "anonymous",
  });
  const { refresh } = history;
  const filterOptions = collectSessionFilterOptions(history.sessions);
  const selectedBrand = activeFilters.brand?.trim() ?? "";
  const modelOptions = selectedBrand ? (filterOptions.modelsByBrand[selectedBrand] ?? filterOptions.allModels) : filterOptions.allModels;
  const mergedDocumentTypeOptions = mergeOptionValues(documentTypeOptions, filterOptions.documentTypes);
  const mergedLanguageOptions = mergeOptionValues(languageOptions, filterOptions.languages, {
    "zh-CN": "中文（简体）",
    "en-US": "英文（美国）",
  });

  useEffect(() => {
    if (!session) return;
    void refresh();
  }, [refresh, session]);

  async function createSession() {
    if (!session) return;
    setActionError(null);
    try {
      const created = await history.create({
        title: "未命名会话",
        messages: [],
        filters: normalizeContextFilters(activeFilters),
        has_image: false,
      });
      onActiveSessionChange(created.id);
    } catch {
      setActionError("会话历史暂时无法保存");
    }
  }

  function selectSession(sessionId: string, filters: Record<string, unknown>) {
    onActiveSessionChange(sessionId);
    onActiveFiltersChange(normalizeContextFilters(filters));
  }

  function updateFilter(key: keyof AgroMechContextFilters, value: string) {
    const nextFilters = normalizeContextFilters({
      ...activeFilters,
      [key]: value,
    });
    onActiveFiltersChange(nextFilters);
    persistFilters(nextFilters);
  }

  function clearFilters() {
    onActiveFiltersChange({});
    persistFilters({});
  }

  function persistFilters(filters: AgroMechContextFilters) {
    if (!activeSessionId) return;
    void history.update(activeSessionId, { filters }).catch(() => {
      setActionError("会话历史暂时无法保存");
    });
  }

  return (
    <div className="grid min-h-0 flex-1 gap-4 bg-surface-canvas p-3 md:p-4 xl:grid-cols-[18rem_minmax(0,1fr)_20rem]">
      <aside
        className="min-h-0 rounded-lg border border-border bg-surface-panel p-3 xl:h-[calc(100dvh-2rem)]"
        data-workbench-region="sessions"
      >
        <div className="flex items-center justify-between gap-3">
          <div>
            <p className="text-xs font-semibold uppercase tracking-[0.16em] text-text-muted">Sessions</p>
            <h2 className="text-base font-semibold">会话历史</h2>
          </div>
          <Button size="sm" type="button" onClick={createSession}>
            <Plus className="size-4" />
            新建会话
          </Button>
        </div>

        {history.error || actionError ? (
          <p className="mt-3 flex items-center gap-2 rounded-lg border border-status-warning/30 bg-status-warning/10 px-3 py-2 text-xs text-status-warning">
            <WarningCircle className="size-4" />
            {actionError ?? history.error}
          </p>
        ) : null}

        <div className="mt-4 grid max-h-72 gap-2 overflow-y-auto pr-1 xl:max-h-[calc(100dvh-11rem)]">
          {history.sessions.length ? (
            history.sessions.map((item) => {
              const active = item.id === activeSessionId;

              return (
                <button
                  className={[
                    "rounded-lg border px-3 py-2 text-left text-sm transition",
                    active
                      ? "border-primary bg-primary/10 text-foreground"
                      : "border-border bg-surface-raised text-text-muted hover:text-foreground",
                  ].join(" ")}
                  key={item.id}
                  type="button"
                  onClick={() => selectSession(item.id, item.filters)}
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="truncate font-medium">{item.title || "未命名会话"}</span>
                    {item.has_image ? <Badge tone="info">图片</Badge> : null}
                  </span>
                  <span className="mt-1 block truncate text-xs opacity-75">{sessionSummary(item)}</span>
                </button>
              );
            })
          ) : (
            <EmptyState
              className="py-8"
              icon={<ClockCounterClockwise className="size-5" />}
              title={history.loading ? "正在加载会话" : "暂无历史会话"}
              description="新建会话后，当前排查过程会出现在这里。"
            />
          )}
        </div>
      </aside>

      <section className="grid min-h-[72dvh] min-w-0 grid-rows-[auto_minmax(0,1fr)] rounded-lg border border-border bg-surface-raised shadow-sm xl:h-[calc(100dvh-2rem)]" data-workbench-region="conversation">
        <header className="border-b border-border bg-surface-panel/80 px-4 py-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <div>
              <p className="text-xs font-semibold uppercase tracking-[0.16em] text-text-muted">Repair Assistant</p>
              <h1 className="text-lg font-semibold">农机维修问答工作台</h1>
            </div>
            <Badge tone={activeSessionId ? "success" : "neutral"}>
              <ChatCircleText className="size-3.5" />
              {activeSessionId ? "已选择会话" : "新会话"}
            </Badge>
          </div>
          <div className="mt-3 rounded-lg border border-border bg-surface-canvas p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex items-center gap-2 text-sm">
                <Database className="size-4 text-primary" />
                <span className="font-medium text-foreground">资料上下文</span>
                <span className="text-xs text-text-muted">当前筛选会随会话保存并进入问答请求。</span>
              </div>
              <Button size="sm" variant="outline" type="button" onClick={clearFilters}>
                清空筛选
              </Button>
            </div>
            <div className="mt-3 grid gap-2 md:grid-cols-4">
              <SearchableSelectField
                label="品牌"
                value={activeFilters.brand ?? ""}
                options={filterOptions.brands}
                placeholder="选择品牌或直接输入"
                onChange={(value) => updateFilter("brand", value)}
              />
              <SearchableSelectField
                label="型号"
                value={activeFilters.model ?? ""}
                options={modelOptions}
                placeholder="选择型号或直接输入"
                onChange={(value) => updateFilter("model", value)}
              />
              <SelectField
                label="资料类型"
                value={activeFilters.document_type ?? ""}
                options={mergedDocumentTypeOptions}
                onChange={(value) => updateFilter("document_type", value)}
              />
              <SelectField
                label="语言"
                value={activeFilters.language ?? ""}
                options={mergedLanguageOptions}
                onChange={(value) => updateFilter("language", value)}
              />
            </div>
            <p className="mt-2 text-xs text-text-muted">
              品牌和型号支持选择或直接输入，无匹配项时可按回车使用当前输入。
            </p>
          </div>
        </header>
        <div className="min-h-0">{children}</div>
      </section>

      <aside
        className={[
          "rounded-lg border border-border bg-surface-panel p-4 xl:h-[calc(100dvh-2rem)]",
          selectedEvidence
            ? "max-xl:fixed max-xl:inset-x-3 max-xl:bottom-3 max-xl:z-40 max-xl:max-h-[82dvh] max-xl:overflow-y-auto max-xl:shadow-2xl"
            : "",
        ].join(" ")}
        data-workbench-region="evidence"
      >
        <EvidencePanel
          payload={selectedEvidence?.payload}
          activeIndex={selectedEvidence?.citationIndex ?? 0}
          previewToken={session?.token}
          onSelect={onEvidenceSelect}
          onClose={selectedEvidence ? onEvidenceClose : undefined}
        />
      </aside>
    </div>
  );
}

function sessionSummary(session: { messages: unknown[]; updated_at: string }): string {
  const updatedAt = session.updated_at ? new Date(session.updated_at) : null;
  const updatedLabel = updatedAt && !Number.isNaN(updatedAt.getTime()) ? updatedAt.toLocaleString("zh-CN") : "时间未知";
  return `${session.messages.length} 条消息 · ${updatedLabel}`;
}

function normalizeContextFilters(filters: Record<string, unknown> | AgroMechContextFilters): AgroMechContextFilters {
  const normalized: AgroMechContextFilters = {};
  contextFilterFields.forEach((field) => {
    const rawValue = filters[field.key];
    const value = typeof rawValue === "string" ? rawValue.trim() : "";
    if (value) {
      normalized[field.key] = value;
    }
  });
  return normalized;
}
