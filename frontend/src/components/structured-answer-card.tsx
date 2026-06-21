import { Alert, AlertDescription, AlertTitle } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { VisualAnnotationPreview } from "@/components/visual-annotation";
import type { AgroMechCitation, AgroMechStructuredPayload } from "@/lib/agromech-chat";

export function StructuredAnswerCard({
  payload,
  onCitationSelect,
}: {
  payload: AgroMechStructuredPayload;
  onCitationSelect?: (citation: AgroMechCitation, index: number, payload: AgroMechStructuredPayload) => void;
}) {
  const answer = payload.answer.trim() || "未返回回答内容。";
  const uncertaintyReasons = payload.uncertainty.reasons.filter(Boolean);

  return (
    <article className="my-3 grid gap-3 rounded-lg border border-border bg-surface-panel p-4 text-sm shadow-sm" data-structured-answer>
      <section className="grid gap-2">
        <p className="leading-7 text-foreground">{answer}</p>
      </section>

      {payload.safety_warnings.length ? (
        <Alert tone="warning">
          <AlertTitle>安全提醒</AlertTitle>
          <AlertDescription>{payload.safety_warnings.join("；")}</AlertDescription>
        </Alert>
      ) : null}

      <section className="grid gap-2 rounded-lg border border-border bg-surface-raised p-3">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-medium text-foreground">不确定性</span>
          <Badge tone={uncertaintyTone(payload.uncertainty.level)}>{payload.uncertainty.level}</Badge>
        </div>
        {uncertaintyReasons.length ? (
          <p className="text-sm leading-6 text-text-muted">{uncertaintyReasons.join("、")}</p>
        ) : (
          <p className="text-sm text-text-muted">未返回不确定性原因。</p>
        )}
      </section>

      {payload.visual_observation?.trim() ? (
        <StructuredSection title="视觉观察" body={payload.visual_observation.trim()} />
      ) : null}

      {payload.question_image ? (
        <VisualAnnotationPreview
          image={payload.question_image}
          annotations={payload.visual_annotations ?? []}
          status={payload.visual_annotation_status}
        />
      ) : null}

      {payload.ocr_text?.trim() ? <StructuredSection title="OCR" body={payload.ocr_text.trim()} /> : null}

      <section className="grid gap-2 rounded-lg border border-border bg-surface-raised p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <h3 className="font-medium text-foreground">引用来源</h3>
          {payload.trace_id ? <Badge tone="neutral">Trace {payload.trace_id}</Badge> : null}
        </div>
        {payload.citations.length ? (
          <div className="grid gap-2">
            {payload.citations.map((citation, index) => (
              <CitationEntry
                citation={citation}
                index={index}
                payload={payload}
                key={`${citation.document_id ?? "unknown"}-${citation.chunk_id ?? index}`}
                onCitationSelect={onCitationSelect}
              />
            ))}
          </div>
        ) : (
          <p className="text-sm text-text-muted">未返回可引用来源。</p>
        )}
      </section>
    </article>
  );
}

function StructuredSection({ title, body }: { title: string; body: string }) {
  return (
    <section className="grid gap-1 rounded-lg border border-border bg-surface-raised p-3">
      <h3 className="font-medium text-foreground">{title}</h3>
      <p className="whitespace-pre-wrap text-sm leading-6 text-text-muted">{body}</p>
    </section>
  );
}

function CitationEntry({
  citation,
  index,
  payload,
  onCitationSelect,
}: {
  citation: AgroMechCitation;
  index: number;
  payload: AgroMechStructuredPayload;
  onCitationSelect?: (citation: AgroMechCitation, index: number, payload: AgroMechStructuredPayload) => void;
}) {
  return (
    <div className="grid gap-2 rounded-lg border border-border bg-surface-panel p-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate font-medium text-foreground">
            {index + 1}. {citation.document_title}
          </p>
          <p className="mt-1 text-xs text-text-muted">{formatLocator(citation.source_locator)}</p>
        </div>
        <Badge tone={citation.accessible ? "success" : "danger"}>{citation.accessible ? "可访问" : "不可访问"}</Badge>
      </div>
      <p className="line-clamp-3 text-sm leading-6 text-text-muted">{citation.evidence_snippet}</p>
      <Button
        className="w-fit"
        size="sm"
        variant="outline"
        type="button"
        onClick={() => onCitationSelect?.(citation, index, payload)}
      >
        查看证据
      </Button>
    </div>
  );
}

function formatLocator(locator: Record<string, unknown>): string {
  const entries = Object.entries(locator);
  if (!entries.length) return "来源定位不可用";
  return entries.map(([key, value]) => `${key}: ${String(value)}`).join(" · ");
}

function uncertaintyTone(level: string): "neutral" | "info" | "success" | "warning" | "danger" {
  const normalized = level.toLowerCase();
  if (normalized === "low") return "success";
  if (normalized === "medium") return "warning";
  if (normalized === "high") return "danger";
  return "info";
}
