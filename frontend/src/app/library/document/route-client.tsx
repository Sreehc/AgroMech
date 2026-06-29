"use client";

import { useSearchParams } from "next/navigation";

import { AppFrame } from "@/components/app-frame";
import { DocumentDetailPage } from "@/components/document-detail-page";
import { MissingDocumentIdState } from "./page";

export function DocumentDetailRouteClient() {
  const searchParams = useSearchParams();
  const documentId = searchParams.get("id")?.trim();

  return (
    <AppFrame>
      {documentId ? (
        <DocumentDetailPage documentId={documentId} />
      ) : (
        <MissingDocumentIdState />
      )}
    </AppFrame>
  );
}
