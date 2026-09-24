import { OctagonAlert, RotateCw } from "lucide-react";

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface ErrorStateProps {
  /** What failed, e.g. "Architecture generation failed." */
  title: string;
  /** Why, in plain language (spec §49). */
  message: string;
  requestId?: string;
  /** Reassure the user that nothing was changed. */
  noChangesApplied?: boolean;
  onRetry?: () => void;
  /** Technical details behind a "View details" disclosure. */
  details?: string;
  className?: string;
}

export function ErrorState({
  title,
  message,
  requestId,
  noChangesApplied = false,
  onRetry,
  details,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn("flex gap-3 rounded-md border border-danger/40 bg-danger-soft px-4 py-3", className)}
    >
      <OctagonAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-fg" />
      <div className="flex min-w-0 flex-1 flex-col gap-2 text-sm">
        <p className="font-semibold text-danger-fg">{title}</p>
        <p className="text-fg-secondary">{message}</p>
        {noChangesApplied ? <p className="font-medium text-fg">No changes were applied.</p> : null}
        {requestId ? (
          <p className="text-xs text-muted">
            Request ID: <span className="tabular text-fg-secondary">{requestId}</span>
          </p>
        ) : null}
        {onRetry || details ? (
          <div className="flex flex-col gap-2">
            {onRetry ? (
              <div>
                <Button size="sm" onClick={onRetry}>
                  <RotateCw aria-hidden className="size-3.5" />
                  Retry
                </Button>
              </div>
            ) : null}
            {details ? (
              <details className="group">
                <summary className="w-fit cursor-pointer text-xs font-medium text-fg-secondary hover:text-fg">
                  View details
                </summary>
                <pre className="tabular mt-2 max-h-48 overflow-auto rounded-sm border border-default bg-sunken p-2 text-xs whitespace-pre-wrap text-fg-secondary">
                  {details}
                </pre>
              </details>
            ) : null}
          </div>
        ) : null}
      </div>
    </div>
  );
}
