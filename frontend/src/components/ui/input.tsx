import * as React from "react"
import { cva, type VariantProps } from "class-variance-authority"

import { cn } from "@/lib/utils"

const inputVariants = cva(
  "flex h-9 w-full rounded-lg border border-input bg-surface-raised px-3 py-2 text-sm text-foreground shadow-xs outline-none transition placeholder:text-text-muted focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/30 disabled:cursor-not-allowed disabled:opacity-50 aria-invalid:border-destructive aria-invalid:ring-destructive/20",
  {
    variants: {
      size: {
        default: "h-9",
        sm: "h-8 text-xs",
        lg: "h-10 text-base",
      },
      state: {
        default: "",
        invalid: "border-destructive ring-3 ring-destructive/20",
      },
    },
    defaultVariants: {
      size: "default",
      state: "default",
    },
  }
)

function Input({
  className,
  size,
  state,
  ...props
}: React.ComponentProps<"input"> & VariantProps<typeof inputVariants>) {
  return (
    <input
      data-slot="input"
      className={cn(inputVariants({ size, state, className }))}
      aria-invalid={state === "invalid" || props["aria-invalid"]}
      {...props}
    />
  )
}

export { Input, inputVariants }
