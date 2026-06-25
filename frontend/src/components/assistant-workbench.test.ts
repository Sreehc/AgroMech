import { existsSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readProjectFile(pathFromFrontendRoot: string): string {
  return readFileSync(
    new URL(`../../${pathFromFrontendRoot}`, import.meta.url),
    "utf8",
  );
}

describe("assistant workbench layout", () => {
  it("mounts the assistant inside a maintenance workbench shell on the home page", () => {
    const page = readProjectFile("src/app/page.tsx");

    expect(page).toContain("AssistantWorkbench");
    expect(page).toContain("<Assistant");
    expect(page).toContain("sessionId=");
  });

  it("provides session history, context, conversation, and evidence regions", () => {
    expect(
      existsSync(new URL("./assistant-workbench.tsx", import.meta.url)),
    ).toBe(true);

    const workbench = readProjectFile("src/components/assistant-workbench.tsx");

    expect(workbench).toContain("useChatSessionHistory");
    expect(workbench).toContain("新建会话");
    expect(workbench).toContain("会话历史");
    expect(workbench).toContain("资料上下文");
    expect(workbench).toContain("品牌");
    expect(workbench).toContain("型号");
    expect(workbench).toContain("资料类型");
    expect(workbench).toContain("语言");
    expect(workbench).toContain("可直接输入品牌或型号；没有匹配项时按回车即可。");
    expect(workbench).toContain("清空筛选");
    expect(workbench).toContain("onActiveFiltersChange");
    expect(workbench).toContain("history.update");
    expect(workbench).toContain("EvidencePanel");
    expect(workbench).toContain("selectedEvidence");
    expect(workbench).toContain("onEvidenceClose");
    expect(workbench).toContain('data-workbench-region="conversation"');
    expect(workbench).toContain('data-workbench-region="evidence"');
    expect(workbench).toContain(
      "rounded-2xl border border-border bg-surface-panel/65",
    );
    expect(workbench).toContain(
      "border-b border-border/80 bg-surface-raised/80",
    );
    expect(workbench).toContain(
      "rounded-xl border border-border/70 bg-surface-raised/85",
    );
    expect(workbench).not.toContain("bg-white/75");
    expect(workbench).not.toContain("bg-white/78");
  });

  it("keeps assistant-ui runtime and thread intact while accepting the active session id", () => {
    const assistant = readProjectFile("src/app/assistant.tsx");
    const page = readProjectFile("src/app/page.tsx");

    expect(assistant).toContain("AssistantRuntimeProvider");
    expect(assistant).toContain("AssistantChatTransport");
    expect(assistant).toContain("<Thread");
    expect(assistant).toContain("makeAssistantDataUI");
    expect(assistant).toContain("agromech-payload");
    expect(assistant).toContain("StructuredAnswerCard");
    expect(assistant).toContain("onCitationSelect");
    expect(assistant).toContain("sessionId");
    expect(assistant).toContain("filters");
    expect(page).toContain("activeFilters");
    expect(page).toContain("selectedEvidence");
    expect(page).toContain("onCitationSelect");
    expect(page).toContain("filters={activeFilters}");
    expect(assistant).toContain("prepareSendMessagesRequest");
  });

  it("passes context filters and session id through the assistant transport", () => {
    const assistant = readProjectFile("src/app/assistant.tsx");

    expect(assistant).toContain("filters: filters ?? {}");
    expect(assistant).toContain("session_id: sessionId");
    expect(assistant).toContain("[filters, sessionId]");
  });

  it("persists and clears context filters with the active chat session", () => {
    const workbench = readProjectFile("src/components/assistant-workbench.tsx");

    expect(workbench).toContain("normalizeContextFilters(activeFilters)");
    expect(workbench).toContain("persistFilters(nextFilters)");
    expect(workbench).toContain("history.update(activeSessionId, { filters })");
    expect(workbench).toContain("onActiveFiltersChange({})");
    expect(workbench).toContain("persistFilters({})");
    expect(workbench).not.toContain("无匹配项时按回车使用当前输入");
  });

  it("wires citation selection to evidence panel open, switch, and close state", () => {
    const page = readProjectFile("src/app/page.tsx");
    const workbench = readProjectFile("src/components/assistant-workbench.tsx");

    expect(page).toContain("const [selectedEvidence, setSelectedEvidence]");
    expect(page).toContain("onCitationSelect={setSelectedEvidence}");
    expect(page).toContain(
      "setSelectedEvidence((current) => (current ? { ...current, citationIndex } : current))",
    );
    expect(page).toContain("onEvidenceClose={() => setSelectedEvidence(null)}");
    expect(workbench).toContain("selectedEvidence?.payload");
    expect(workbench).toContain("selectedEvidence?.citationIndex");
    expect(workbench).toContain(
      "onClose={selectedEvidence ? onEvidenceClose : undefined}",
    );
  });
});
