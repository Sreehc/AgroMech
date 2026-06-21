"use client";

import { AssistantRuntimeProvider, makeAssistantDataUI } from "@assistant-ui/react";
import {
  useChatRuntime,
  AssistantChatTransport,
} from "@assistant-ui/react-ai-sdk";
import { lastAssistantMessageIsCompleteWithToolCalls } from "ai";
import { createContext, useContext, useMemo } from "react";

import { Thread } from "@/components/assistant-ui/thread";
import { StructuredAnswerCard } from "@/components/structured-answer-card";
import type { AgroMechContextFilters, AgroMechEvidenceSelection, AgroMechStructuredPayload } from "@/lib/agromech-chat";

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
  filters,
  onCitationSelect,
}: {
  sessionId?: string;
  filters?: AgroMechContextFilters;
  onCitationSelect?: (selection: AgroMechEvidenceSelection) => void;
}) => {
  const transport = useMemo(
    () =>
      new AssistantChatTransport({
        api: "/api/chat",
        prepareSendMessagesRequest: async (options) => {
          return {
            body: {
              ...options.body,
              id: options.id,
              messages: options.messages,
              trigger: options.trigger,
              messageId: options.messageId,
              metadata: options.requestMetadata,
              filters: filters ?? {},
              ...(sessionId ? { session_id: sessionId } : {}),
            },
          };
        },
      }),
    [filters, sessionId],
  );
  const runtime = useChatRuntime({
    sendAutomaticallyWhen: lastAssistantMessageIsCompleteWithToolCalls,
    transport,
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
