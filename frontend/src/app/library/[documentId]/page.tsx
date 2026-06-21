import { AppFrame } from "@/components/app-frame";
import { DocumentDetailPage } from "@/components/document-detail-page";

export default async function Page({ params }: { params: Promise<{ documentId: string }> }) {
  const { documentId } = await params;

  return (
    <AppFrame>
      <DocumentDetailPage documentId={documentId} />
    </AppFrame>
  );
}
