import { AppShell } from "@/components/app-shell";
import { LibraryPage } from "@/components/library-page";

export default function Page() {
  return (
    <AppShell view="library">
      <LibraryPage />
    </AppShell>
  );
}
