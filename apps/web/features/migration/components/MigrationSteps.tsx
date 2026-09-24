import {
  CheckCircle2,
  ChevronRight,
  Circle,
  CircleDot,
  Clock,
  RotateCcw,
  type LucideIcon,
} from "lucide-react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import { RiskBadge } from "@/features/evolution/components/RiskBadge";
import { cn } from "@/lib/utils";
import type { MigrationStep } from "@/types/evolution";

export const STEP_STATUS_META: Record<
  MigrationStep["status"],
  { label: string; tone: BadgeTone; Icon: LucideIcon }
> = {
  pending: { label: "Pending", tone: "neutral", Icon: Circle },
  in_progress: { label: "In progress", tone: "info", Icon: CircleDot },
  done: { label: "Done", tone: "accent", Icon: CheckCircle2 },
};

export function migrationStepAnchor(stepId: string): string {
  return `migration-step-${stepId}`;
}

export interface MigrationStepsProps {
  steps: readonly MigrationStep[];
  nodeName: (id: string) => string;
}

/** Dependency-ordered migration steps, each with its own rollback (presentational). */
export function MigrationSteps({ steps, nodeName }: MigrationStepsProps) {
  const ordered = [...steps].sort((a, b) => a.order - b.order);
  const byId = new Map(steps.map((s) => [s.id, s]));

  return (
    <ol aria-label="Migration steps" className="flex flex-col">
      {ordered.map((step, index) => {
        const status = STEP_STATUS_META[step.status];
        const deps = step.dependsOn.map((id) => byId.get(id)).filter((s): s is MigrationStep => Boolean(s));
        const last = index === ordered.length - 1;
        return (
          <li
            key={step.id}
            id={migrationStepAnchor(step.id)}
            className="relative flex scroll-mt-20 gap-3 pb-6 last:pb-0"
          >
            {!last ? (
              <span aria-hidden className="absolute top-8 bottom-0 left-[13px] w-px bg-default" />
            ) : null}
            <span
              aria-hidden
              className={cn(
                "tabular relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border text-xs font-semibold",
                step.status === "done"
                  ? "border-accent/40 bg-accent-soft text-accent-fg"
                  : step.status === "in_progress"
                    ? "border-info/40 bg-info-soft text-info-fg"
                    : "border-strong bg-surface text-fg",
              )}
            >
              {step.order}
            </span>

            <div className="flex min-w-0 flex-1 flex-col gap-2 pt-0.5">
              <div className="flex flex-col gap-1.5 sm:flex-row sm:items-start sm:justify-between sm:gap-3">
                <h3 className="text-sm font-semibold text-fg">
                  <span className="sr-only">Step {step.order}: </span>
                  {step.title}
                </h3>
                <div className="flex shrink-0 flex-wrap items-center gap-1.5">
                  <Badge tone={status.tone}>
                    <status.Icon aria-hidden />
                    {status.label}
                  </Badge>
                  <RiskBadge risk={step.risk} />
                  <Badge tone="neutral">
                    <Clock aria-hidden />
                    <span className="sr-only">Estimated duration: </span>
                    {step.estimatedDuration}
                  </Badge>
                </div>
              </div>

              <p className="text-sm text-fg-secondary">{step.description}</p>

              <dl className="flex flex-col gap-1.5 text-xs">
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <dt className="text-muted">Depends on</dt>
                  <dd className="text-fg-secondary">
                    {deps.length === 0 ? (
                      "Nothing — can start first"
                    ) : (
                      <span className="flex flex-wrap gap-x-2 gap-y-1">
                        {deps.map((dep) => (
                          <a
                            key={dep.id}
                            href={`#${migrationStepAnchor(dep.id)}`}
                            className="tabular rounded-sm font-medium text-fg underline decoration-strong underline-offset-2 hover:decoration-fg"
                          >
                            Step {dep.order}
                            <span className="sr-only">: {dep.title}</span>
                          </a>
                        ))}
                      </span>
                    )}
                  </dd>
                </div>
                {step.nodeIds.length > 0 ? (
                  <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                    <dt className="text-muted">Affects</dt>
                    <dd>
                      <ul className="flex flex-wrap gap-1">
                        {step.nodeIds.map((id) => (
                          <li
                            key={id}
                            className="rounded-sm border border-default bg-surface-2 px-1.5 py-0.5 text-fg-secondary"
                          >
                            {nodeName(id)}
                          </li>
                        ))}
                      </ul>
                    </dd>
                  </div>
                ) : null}
              </dl>

              <details className="group rounded-sm border border-default bg-sunken">
                <summary className="flex cursor-pointer list-none items-center gap-1.5 px-2.5 py-1.5 text-xs font-medium text-fg-secondary hover:text-fg [&::-webkit-details-marker]:hidden">
                  <ChevronRight
                    aria-hidden
                    className="size-3.5 transition-transform group-open:rotate-90 motion-reduce:transition-none"
                  />
                  <RotateCcw aria-hidden className="size-3.5" />
                  Rollback
                  <span className="sr-only"> for step {step.order}</span>
                </summary>
                <p className="border-t border-default px-2.5 py-2 text-sm text-fg-secondary">
                  {step.rollback}
                </p>
              </details>
            </div>
          </li>
        );
      })}
    </ol>
  );
}
