/* eslint-disable @next/next/no-img-element */

import { ImageSquare, WarningCircle } from "@phosphor-icons/react";

import { Badge } from "@/components/ui/badge";
import type {
  AgroMechImageAttachment,
  AgroMechVisualAnnotation,
  AgroMechVisualAnnotationStatus,
} from "@/lib/agromech-chat";

export function VisualAnnotationPreview({
  image,
  annotations = [],
  status,
}: {
  image: AgroMechImageAttachment;
  annotations?: AgroMechVisualAnnotation[];
  status?: AgroMechVisualAnnotationStatus;
}) {
  const validAnnotations = annotations.filter(hasNormalizedBox);
  const missingReason =
    status?.missing_reason ??
    (!validAnnotations.length ? "no_usable_bbox" : null);

  return (
    <section
      className="grid gap-3 rounded-2xl border border-border bg-surface-panel/65 p-3"
      data-visual-annotation
    >
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div>
          <div className="flex items-center gap-2">
            <ImageSquare className="size-4 text-primary" />
            <h3 className="font-medium text-foreground">现场图片</h3>
          </div>
          <p className="mt-1 text-xs text-text-muted">{image.filename}</p>
        </div>
        <Badge tone={validAnnotations.length ? "success" : "warning"}>
          {validAnnotations.length ? "已标注" : "缺少坐标"}
        </Badge>
      </div>

      <figure className="relative overflow-hidden rounded-xl border border-border/70 bg-black">
        <img
          className="block w-full object-contain"
          src={image.dataUrl}
          alt={`${image.filename} 缩略图`}
        />
        {validAnnotations.map((annotation) => (
          <span
            aria-label={`${annotation.label} 标注框`}
            className="absolute rounded-sm border-2 border-status-warning bg-status-warning/20 shadow-[0_0_0_1px_rgba(0,0,0,0.35)]"
            key={annotation.id}
            role="img"
            style={bboxStyle(annotation.bbox)}
          >
            <span className="absolute left-0 top-0 max-w-48 -translate-y-full rounded-t-md bg-status-warning px-2 py-1 text-xs font-semibold text-black">
              {annotation.label}
              {typeof annotation.confidence === "number"
                ? ` ${Math.round(annotation.confidence * 100)}%`
                : ""}
            </span>
          </span>
        ))}
      </figure>

      {validAnnotations.length ? (
        <div className="flex flex-wrap gap-2">
          {validAnnotations.map((annotation) => (
            <Badge key={`${annotation.id}-badge`} tone="info">
              {annotation.label}
              {typeof annotation.confidence === "number"
                ? ` ${Math.round(annotation.confidence * 100)}%`
                : ""}
            </Badge>
          ))}
        </div>
      ) : (
        <div className="rounded-2xl border border-status-warning/30 bg-status-warning/10 p-3 text-sm text-status-warning">
          <p className="flex items-center gap-2 font-medium">
            <WarningCircle className="size-4" />
            视觉标注数据缺失
          </p>
          <p className="mt-1 text-status-warning/85">
            {missingReason || "未返回可用于框选的坐标。"}
          </p>
          {annotations.length ? (
            <div className="mt-2 flex flex-wrap gap-2">
              {annotations.map((annotation) => (
                <Badge key={`${annotation.id}-fallback`} tone="warning">
                  {annotation.label}
                </Badge>
              ))}
            </div>
          ) : null}
        </div>
      )}
    </section>
  );
}

function hasNormalizedBox(
  annotation: AgroMechVisualAnnotation,
): annotation is AgroMechVisualAnnotation & {
  bbox: NonNullable<AgroMechVisualAnnotation["bbox"]>;
} {
  const box = annotation.bbox;
  return Boolean(
    box &&
    box.format === "normalized_xywh" &&
    Number.isFinite(box.x) &&
    Number.isFinite(box.y) &&
    Number.isFinite(box.width) &&
    Number.isFinite(box.height) &&
    box.width > 0 &&
    box.height > 0,
  );
}

function bboxStyle(box: NonNullable<AgroMechVisualAnnotation["bbox"]>) {
  return {
    left: `${asPercent(box.x)}%`,
    top: `${asPercent(box.y)}%`,
    width: `${asPercent(box.width)}%`,
    height: `${asPercent(box.height)}%`,
  };
}

function asPercent(value: number): number {
  const percent = value <= 1 ? value * 100 : value;
  return Math.max(0, Math.min(100, Math.round(percent * 100) / 100));
}
