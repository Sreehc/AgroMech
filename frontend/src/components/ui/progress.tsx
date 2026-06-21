import * as React from "react"

import { cn } from "@/lib/utils"

function clampProgressValue(value: number): number {
  if (Number.isNaN(value)) return 0
  return Math.min(100, Math.max(0, value))
}

function Progress({
  className,
  value,
  label,
  ...props
}: Omit<React.ComponentProps<"div">, "children"> & {
  value?: number
  label?: string
}) {
  const normalizedValue = value === undefined ? undefined : clampProgressValue(value)
  const indeterminate = normalizedValue === undefined

  return (
    <div
      data-slot="progress"
      className={cn("grid gap-1.5", className)}
      {...props}
    >
      {label ? (
        <div className="flex items-center justify-between text-xs text-text-muted">
          <span>{label}</span>
          {normalizedValue !== undefined ? <span>{normalizedValue}%</span> : null}
        </div>
      ) : null}
      <div
        className="h-2 overflow-hidden rounded-full bg-muted"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={normalizedValue}
      >
        <div
          className={cn(
            "h-full rounded-full bg-primary transition-all",
            indeterminate && "w-1/3 animate-pulse"
          )}
          style={indeterminate ? undefined : { width: `${normalizedValue}%` }}
        />
      </div>
    </div>
  )
}

export { Progress, clampProgressValue }
