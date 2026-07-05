import { existsSync, readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

function readFrontendFile(path: string): string {
  return readFileSync(new URL(`../../${path}`, import.meta.url), "utf8");
}

describe("static frontend deployment contract", () => {
  it("builds as a static Next export without server routes", () => {
    const nextConfig = readFrontendFile("next.config.ts");

    // 生产构建必须是纯静态导出。dev 下为了本地 /backend 代理关闭 export、
    // 启用 rewrites，因此 export 与 rewrites 都必须由 isDev 门控，
    // 保证 `next build`（NODE_ENV=production）时只有 export、没有 server route。
    expect(nextConfig).toContain('output: "export"');
    expect(nextConfig).toContain("const isDev = process.env.NODE_ENV === \"development\"");
    expect(nextConfig).toContain("isDev ? {} : { output: \"export\" }");
    expect(nextConfig).toMatch(/isDev[\s\S]*rewrites\(\)/);
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
