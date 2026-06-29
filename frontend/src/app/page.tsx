"use client";

import { useState } from "react";

import { Assistant } from "@/app/assistant";
import { AppFrame } from "@/components/app-frame";
import { AssistantWorkbench } from "@/components/assistant-workbench";
import type { AgroMechContextFilters, AgroMechEvidenceSelection } from "@/lib/agromech-chat";

export default function Home() {
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeFilters, setActiveFilters] = useState<AgroMechContextFilters>({});
  const [selectedEvidence, setSelectedEvidence] = useState<AgroMechEvidenceSelection | null>(null);

  return (
    <AppFrame>
      <AssistantWorkbench
        activeSessionId={activeSessionId}
        onActiveSessionChange={setActiveSessionId}
        activeFilters={activeFilters}
        onActiveFiltersChange={setActiveFilters}
        selectedEvidence={selectedEvidence}
        onEvidenceSelect={(citationIndex) =>
          setSelectedEvidence((current) => (current ? { ...current, citationIndex } : current))
        }
        onEvidenceClose={() => setSelectedEvidence(null)}
      >
        {(session) => (
          <Assistant
            sessionId={activeSessionId ?? undefined}
            token={session?.token}
            filters={activeFilters}
            onCitationSelect={setSelectedEvidence}
          />
        )}
      </AssistantWorkbench>
    </AppFrame>
  );
}
