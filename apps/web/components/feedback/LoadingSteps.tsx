import { Check, Circle, CircleDot, X } from "lucide-react";

import { cn } from "@/lib/utils";

export type LoadingStepStatus = "pending" | "running" | "done" | "failed";

export interface LoadingStep {
  id: string;
  label: string;
  status: LoadingStepStatus;
}

export interface LoadingStepsProps {
  steps: readonly LoadingStep[];
  title?: string;
  className?: string;
}

const STATUS_META: Record<
  LoadingStepStatus,
  { srLabel: string; Icon: typeof Check; iconClass: string; labelClass: string }
> = {
  done: { srLabel: "Done", Icon: Check, iconClass: "text-accent-fg", labelClass: "text-fg" },
  running: {
    srLabel: "In progress",
    Icon: CircleDot,
    iconClass: "text-accent motion-safe:animate-pulse",
    labelClass: "font-medium text-fg",
  },
  pending: { srLabel: "Pending", Icon: Circle, iconClass: "text-muted", labelClass: "text-muted" },
  failed: { srLabel: "Failed", Icon: X, iconClass: "text-danger-fg", labelClass: "text-danger-fg" },
};

/** Explicit progress for long AI operations (spec §48, §87). */
export function LoadingSteps({ steps, title, className }: LoadingStepsProps) {
  return (
    <div role="status" aria-live="polite" className={cn("flex flex-col gap-3", className)}>
      {title ? <p className="text-sm font-semibold text-fg">{title}</p> : null}
      <ol className="flex flex-col gap-2">
        {steps.map((step) => {
          const meta = STATUS_META[step.status];
          return (
            <li key={step.id} className="flex items-center gap-2 text-sm">
              <meta.Icon aria-hidden className={cn("size-4 shrink-0", meta.iconClass)} />
              <span className={meta.labelClass}>{step.label}</span>
              <span className="sr-only">: {meta.srLabel}</span>
            </li>
          );
        })}
      </ol>
    </div>
  );
}
