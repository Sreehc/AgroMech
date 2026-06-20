import { Assistant } from "@/app/assistant";
import { AppFrame } from "@/components/app-frame";

export default function Home() {
  return (
    <AppFrame>
      <div className="min-h-0 flex-1">
        <Assistant />
      </div>
    </AppFrame>
  );
}
