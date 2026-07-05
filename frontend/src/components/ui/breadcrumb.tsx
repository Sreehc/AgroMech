"use client";

import * as React from "react";
import Link from "next/link";
import { CaretRight, House } from "@phosphor-icons/react";

import { cn } from "@/lib/utils";

export type BreadcrumbItem = {
  label: string;
  href?: string;
};

function Breadcrumb({
  items,
  homeHref = "/",
  className,
  ...props
}: React.ComponentProps<"nav"> & {
  items: BreadcrumbItem[];
  homeHref?: string;
}) {
  return (
    <nav
      aria-label="面包屑"
      data-slot="breadcrumb"
      className={cn(
        "flex items-center gap-1 text-sm text-text-muted",
        className,
      )}
      {...props}
    >
      <Link
        aria-label="首页"
        href={homeHref}
        className="inline-flex items-center rounded-md p-1 transition hover:text-foreground"
      >
        <House className="size-4" weight="duotone" />
      </Link>
      {items.map((item, index) => {
        const isLast = index === items.length - 1;

        return (
          <React.Fragment key={`${item.label}-${index}`}>
            <CaretRight className="size-3.5 shrink-0 opacity-50" />
            {item.href && !isLast ? (
              <Link
                href={item.href}
                className="rounded-md px-1 transition hover:text-foreground"
              >
                {item.label}
              </Link>
            ) : (
              <span
                aria-current={isLast ? "page" : undefined}
                className={cn(
                  "px-1",
                  isLast ? "font-medium text-foreground" : undefined,
                )}
              >
                {item.label}
              </span>
            )}
          </React.Fragment>
        );
      })}
    </nav>
  );
}

export { Breadcrumb };
