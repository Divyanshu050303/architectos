import type { LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

export interface EmptyStateProps {
  icon?: LucideIcon;
  /** What is missing, e.g. "No architecture yet." */
  title: string;
  /** What to do next (spec §47): never just "No data". */
  description: string;
  action?: React.ReactNode;
  /** Use 1 when the empty state is the whole page (e.g. not-found), so the page keeps a main heading. */
  headingLevel?: 1 | 2 | 3;
  className?: string;
}

export function EmptyState({
  icon: Icon,
  title,
  description,
  action,
  headingLevel = 2,
  className,
}: EmptyStateProps) {
  const Heading = `h${headingLevel}` as const;
  return (
    <div
      className={cn(
        "flex flex-col items-center justify-center gap-3 rounded-md border border-dashed border-default px-6 py-10 text-center",
        className,
      )}
    >
      {Icon ? (
        <span className="flex size-10 items-center justify-center rounded-md bg-surface-2 text-muted">
          <Icon aria-hidden className="size-5" />
        </span>
      ) : null}
      <div className="flex max-w-sm flex-col gap-1">
        <Heading className="text-sm font-semibold text-fg">{title}</Heading>
        <p className="text-sm text-fg-secondary">{description}</p>
      </div>
      {action ? <div className="mt-1 flex gap-2">{action}</div> : null}
    </div>
  );
}
