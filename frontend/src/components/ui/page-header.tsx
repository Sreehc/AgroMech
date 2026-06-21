import * as React from "react"

import { cn } from "@/lib/utils"

function PageHeader({
  className,
  eyebrow,
  title,
  description,
  actions,
  ...props
}: React.ComponentProps<"header"> & {
  eyebrow?: string
  title: string
  description?: string
  actions?: React.ReactNode
}) {
  return (
    <header
      data-slot="page-header"
      className={cn("flex flex-col gap-3 md:flex-row md:items-end md:justify-between", className)}
      {...props}
    >
      <div className="grid gap-1">
        {eyebrow ? (
          <p className="text-xs font-semibold uppercase tracking-[0.18em] text-text-muted">
            {eyebrow}
          </p>
        ) : null}
        <h1 className="text-2xl font-semibold tracking-normal text-foreground">{title}</h1>
        {description ? <p className="max-w-2xl text-sm text-text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  )
}

export { PageHeader }
