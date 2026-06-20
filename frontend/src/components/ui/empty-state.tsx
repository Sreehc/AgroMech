import * as React from "react"

import { cn } from "@/lib/utils"

function EmptyState({
  className,
  title,
  description,
  action,
  icon,
  ...props
}: React.ComponentProps<"section"> & {
  title: string
  description?: string
  action?: React.ReactNode
  icon?: React.ReactNode
}) {
  return (
    <section
      data-slot="empty-state"
      className={cn(
        "grid place-items-center rounded-lg border border-dashed border-border bg-surface-panel/70 px-4 py-10 text-center",
        className
      )}
      {...props}
    >
      <div className="grid max-w-sm place-items-center gap-3">
        {icon ? (
          <div className="grid size-10 place-items-center rounded-full bg-muted text-text-muted">
            {icon}
          </div>
        ) : null}
        <div className="grid gap-1">
          <h3 className="font-medium text-foreground">{title}</h3>
          {description ? <p className="text-sm text-text-muted">{description}</p> : null}
        </div>
        {action ? <div className="mt-1">{action}</div> : null}
      </div>
    </section>
  )
}

export { EmptyState }
