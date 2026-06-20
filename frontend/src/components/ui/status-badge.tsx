import * as React from "react"
import { CheckCircle, CircleNotch, Info, WarningCircle } from "@phosphor-icons/react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"
import {
  documentStatusPresentation,
  type DocumentStatusTone,
} from "@/lib/frontend-api"

const statusBadgeVariants = cva(
  "inline-flex items-center gap-1 rounded-full border px-2.5 py-1 text-xs font-medium whitespace-nowrap",
  {
    variants: {
      tone: {
        neutral: "border-border bg-surface-raised text-text-muted",
        info: "border-status-info/30 bg-status-info/10 text-status-info",
        success: "border-status-success/30 bg-status-success/10 text-status-success",
        warning: "border-status-warning/30 bg-status-warning/10 text-status-warning",
        danger: "border-status-danger/30 bg-status-danger/10 text-status-danger",
      },
    },
    defaultVariants: {
      tone: "neutral",
    },
  }
)

function StatusBadge({
  className,
  status,
  tone,
  children,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof statusBadgeVariants> & {
    status?: string
  }) {
  const presentation = status ? documentStatusPresentation(status) : null
  const resolvedTone = tone ?? presentation?.tone ?? "neutral"

  return (
    <span
      data-slot="status-badge"
      className={cn(statusBadgeVariants({ tone: resolvedTone, className }))}
      title={presentation && !presentation.known ? `未知后端状态：${status}` : props.title}
      {...props}
    >
      <StatusBadgeIcon tone={resolvedTone} />
      {children ?? presentation?.label ?? status}
    </span>
  )
}

function StatusBadgeIcon({ tone }: { tone: DocumentStatusTone }) {
  const className = "size-3.5"
  if (tone === "success") return <CheckCircle className={className} weight="fill" />
  if (tone === "warning") return <WarningCircle className={className} weight="fill" />
  if (tone === "danger") return <WarningCircle className={className} weight="fill" />
  if (tone === "info") return <CircleNotch className={className} weight="bold" />
  return <Info className={className} weight="fill" />
}

export { StatusBadge, statusBadgeVariants }
