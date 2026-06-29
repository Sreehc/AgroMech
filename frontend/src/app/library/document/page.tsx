import { Suspense } from "react";

import { EmptyState } from "@/components/ui/empty-state";
import { DocumentDetailRouteClient } from "./route-client";

export default function DocumentDetailRoutePage() {
  return (
    <Suspense
      fallback={
        <EmptyState
          className="m-6"
          title="正在读取资料 ID"
          description="请稍候。"
        />
      }
    >
      <DocumentDetailRouteClient />
    </Suspense>
  );
}

export function MissingDocumentIdState() {
  return (
    <EmptyState
      className="m-6"
      title="资料 ID 缺失"
      description="请从资料库列表进入资料详情。"
    />
  );
}
