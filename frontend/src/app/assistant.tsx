"use client";

import { AssistantRuntimeProvider, makeAssistantDataUI } from "@assistant-ui/react";
import { useChatRuntime } from "@assistant-ui/react-ai-sdk";
import { lastAssistantMessageIsCompleteWithToolCalls } from "ai";
import { createContext, useContext, useMemo } from "react";

import { Thread } from "@/components/assistant-ui/thread";
import { StructuredAnswerCard } from "@/components/structured-answer-card";
import { createAnonymousHistoryAdapter } from "@/lib/anonymous-chat-store";
import {
  createAgroMechChatTransport,
  type AgroMechContextFilters,
  type AgroMechEvidenceSelection,
  type AgroMechStructuredPayload,
} from "@/lib/agromech-chat";

const CitationSelectionContext = createContext<((selection: AgroMechEvidenceSelection) => void) | undefined>(undefined);

function AgroMechPayloadRenderer({ data }: { data: AgroMechStructuredPayload }) {
  const onCitationSelect = useContext(CitationSelectionContext);

  return (
    <StructuredAnswerCard
      payload={data}
      onCitationSelect={(_citation, citationIndex, payload) => onCitationSelect?.({ payload, citationIndex })}
    />
  );
}

const AgroMechPayloadDataUI = makeAssistantDataUI<AgroMechStructuredPayload>({
  name: "agromech-payload",
  render: ({ data }) => <AgroMechPayloadRenderer data={data} />,
});

export const Assistant = ({
  sessionId,
  token,
  filters,
  onCitationSelect,
}: {
  sessionId?: string;
  token?: string;
  filters?: AgroMechContextFilters;
  onCitationSelect?: (selection: AgroMechEvidenceSelection) => void;
}) => {
  const transport = useMemo(
    () =>
      createAgroMechChatTransport({
        token,
        filters: filters ?? {},
        sessionId,
      }),
    [filters, sessionId, token],
  );
  // 未登录时把对话整段持久化到 localStorage（匿名单会话）；登录用户走后端会话历史。
  const anonymousHistory = useMemo(
    () => (token ? undefined : createAnonymousHistoryAdapter()),
    [token],
  );
  const runtime = useChatRuntime({
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithToolCalls,
    transport,
    adapters: anonymousHistory ? { history: anonymousHistory } : undefined,
  });

  return (
    <AssistantRuntimeProvider runtime={runtime}>
      <CitationSelectionContext.Provider value={onCitationSelect}>
        <AgroMechPayloadDataUI />
        <div className="h-full">
          <Thread />
        </div>
      </CitationSelectionContext.Provider>
    </AssistantRuntimeProvider>
  );
};
