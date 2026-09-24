import { cn } from "@/lib/utils";

export interface PageHeaderProps {
  title: string;
  description?: React.ReactNode;
  /** Primary page actions, right-aligned. */
  actions?: React.ReactNode;
  /** Small context line, e.g. version or last-analyzed time. */
  meta?: React.ReactNode;
  className?: string;
}

export function PageHeader({ title, description, actions, meta, className }: PageHeaderProps) {
  return (
    <header className={cn("flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between", className)}>
      <div className="flex min-w-0 flex-col gap-1">
        <h1 className="truncate text-lg font-semibold text-fg">{title}</h1>
        {description ? <p className="text-sm text-fg-secondary">{description}</p> : null}
        {meta ? <div className="flex flex-wrap items-center gap-2 text-xs text-muted">{meta}</div> : null}
      </div>
      {actions ? <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div> : null}
    </header>
  );
}
