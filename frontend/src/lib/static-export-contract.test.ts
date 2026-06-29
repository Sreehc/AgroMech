import { existsSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readFrontendFile(path: string): string {
  return readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
}

describe("static frontend deployment contract", () => {
  it("builds as a static Next export without server routes", () => {
    const nextConfig = readFrontendFile("next.config.ts");

    expect(nextConfig).toContain('output: "export"');
    expect(nextConfig).not.toContain("rewrites()");
    expect(existsSync(new URL("../app/api/chat/route.ts", import.meta.url))).toBe(false);
  });

  it("sends assistant questions directly to backend QA endpoints", () => {
    const assistant = readFrontendFile("src/app/assistant.tsx");
    const chatAdapter = readFrontendFile("src/lib/agromech-chat.ts");

    expect(assistant).not.toContain('api: "/api/chat"');
    expect(chatAdapter).toContain('"/backend/qa/text"');
    expect(chatAdapter).toContain('"/backend/qa/image"');
    expect(chatAdapter).toContain("createAgroMechChatTransport");
  });

  it("uses a static document detail route and reads the document id on the client", () => {
    expect(existsSync(new URL("../app/library/[documentId]/page.tsx", import.meta.url))).toBe(false);

    const detailPage = readFrontendFile("src/app/library/document/page.tsx");
    const detailClient = readFrontendFile("src/app/library/document/route-client.tsx");
    const libraryPage = readFrontendFile("src/components/library-page.tsx");

    expect(detailPage).toContain("function DocumentDetailRoutePage");
    expect(detailPage).toContain("<Suspense");
    expect(detailClient).toContain("useSearchParams");
    expect(detailClient).toContain("<DocumentDetailPage documentId={documentId}");
    expect(libraryPage).toContain('href={`/library/document?id=${encodeURIComponent(document.id)}`}');
  });
});
