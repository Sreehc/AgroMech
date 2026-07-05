"use client";

import { AppShell } from "@/components/app-shell";
import { ChatPage } from "@/components/chat-page";

export default function Home() {
  return (
    <AppShell view="chat">
      <ChatPage />
    </AppShell>
  );
}
