"use client";

import { useSearchParams } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { DocumentDetailPage } from "@/components/document-detail-page";
import { MissingDocumentIdState } from "./page";

export function DocumentDetailRouteClient() {
  const searchParams = useSearchParams();
  const documentId = searchParams.get("id")?.trim();

  return (
    <AppShell view="library">
      {documentId ? (
        <DocumentDetailPage documentId={documentId} />
      ) : (
        <MissingDocumentIdState />
      )}
    </AppShell>
  );
}
