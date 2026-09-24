import { Check, Circle, CircleDot, Hand, X } from "lucide-react";

import { cn } from "@/lib/utils";

import type { DiscoveryStage, DiscoveryStageStatus } from "../meta";

const STATUS_META: Record<
  DiscoveryStageStatus,
  { srLabel: string; Icon: typeof Check; marker: string; bar: string; label: string }
> = {
  done: {
    srLabel: "Done",
    Icon: Check,
    marker: "border-accent-strong bg-accent-soft text-accent-fg",
    bar: "bg-accent-strong w-full",
    label: "text-fg",
  },
  running: {
    srLabel: "In progress",
    Icon: CircleDot,
    marker: "border-info bg-info-soft text-info-fg motion-safe:animate-pulse",
    bar: "bg-info w-1/2 motion-safe:animate-pulse",
    label: "text-fg font-semibold",
  },
  awaiting: {
    srLabel: "Waiting for you",
    Icon: Hand,
    marker: "border-accent bg-surface text-accent-fg",
    bar: "bg-accent/40 w-full",
    label: "text-fg font-semibold",
  },
  failed: {
    srLabel: "Failed",
    Icon: X,
    marker: "border-danger bg-danger-soft text-danger-fg",
    bar: "bg-danger w-full",
    label: "text-danger-fg font-semibold",
  },
  pending: {
    srLabel: "Pending",
    Icon: Circle,
    marker: "border-default bg-surface text-muted",
    bar: "w-0",
    label: "text-muted",
  },
};

/** CONNECT → DISCOVER → NORMALIZE → GENERATE ARCHITECTURE → REVIEW → SAVE (spec §44, §87). */
export function DiscoveryStepper({ stages }: { stages: readonly DiscoveryStage[] }) {
  const current = stages.find(
    (s) => s.status === "running" || s.status === "awaiting" || s.status === "failed",
  );
  return (
    <nav aria-label="Discovery progress">
      <ol className="grid grid-cols-2 gap-x-3 gap-y-4 sm:grid-cols-3 xl:grid-cols-6" aria-live="polite">
        {stages.map((stage, index) => {
          const meta = STATUS_META[stage.status];
          return (
            <li
              key={stage.id}
              aria-current={stage === current ? "step" : undefined}
              className="flex min-w-0 flex-col gap-2"
            >
              <div className="h-0.5 w-full overflow-hidden rounded-full bg-surface-2" aria-hidden>
                <div className={cn("h-full rounded-full transition-[width]", meta.bar)} />
              </div>
              <div className="flex min-w-0 items-start gap-2">
                <span
                  aria-hidden
                  className={cn(
                    "flex size-6 shrink-0 items-center justify-center rounded-full border [&_svg]:size-3.5",
                    meta.marker,
                  )}
                >
                  {stage.status === "pending" ? (
                    <span className="tabular text-2xs">{index + 1}</span>
                  ) : (
                    <meta.Icon />
                  )}
                </span>
                <div className="flex min-w-0 flex-col">
                  <span className={cn("text-2xs font-semibold tracking-wide uppercase", meta.label)}>
                    {stage.label}
                    <span className="sr-only">: {meta.srLabel}</span>
                  </span>
                  <span className="tabular text-xs break-words text-fg-secondary">
                    {stage.detail ?? (stage.status === "pending" ? "—" : meta.srLabel)}
                  </span>
                </div>
              </div>
            </li>
          );
        })}
      </ol>
    </nav>
  );
}
