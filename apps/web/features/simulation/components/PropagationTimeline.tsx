import { AlertTriangle, Gauge, Timer, TrendingUp, Unplug, Zap, type LucideIcon } from "lucide-react";

import { SIMULATION_PHASES } from "@/schemas/simulation";
import { cn } from "@/lib/utils";
import type { SimulationPhase, SimulationTimelineEvent } from "@/types/simulation";

export const PHASE_META: Record<SimulationPhase, { label: string; Icon: LucideIcon }> = {
  failure: { label: "Failure", Icon: Zap },
  dependency: { label: "Dependency", Icon: Unplug },
  load_increase: { label: "Load increase", Icon: TrendingUp },
  resource_pressure: { label: "Resource pressure", Icon: Gauge },
  latency: { label: "Latency", Icon: Timer },
  potential_failure: { label: "Potential failure", Icon: AlertTriangle },
};

function formatOffset(seconds: number): string {
  if (seconds < 60) return `t+${seconds}s`;
  const m = Math.floor(seconds / 60);
  const s = seconds % 60;
  return s === 0 ? `t+${m}m` : `t+${m}m ${s}s`;
}

export interface PropagationTimelineProps {
  timeline: readonly SimulationTimelineEvent[];
  nodeName: (id: string) => string;
}

/**
 * Failure → Dependency → Load increase → Resource pressure → Latency → Potential failure (spec §41).
 * A static vertical stepper: phases the engine reported are marked reached; the rest are shown muted.
 */
export function PropagationTimeline({ timeline, nodeName }: PropagationTimelineProps) {
  const phases = SIMULATION_PHASES.map((phase) => ({
    phase,
    events: timeline.filter((e) => e.phase === phase),
  }));

  return (
    <ol aria-label="Failure propagation" className="flex flex-col">
      {phases.map(({ phase, events }, index) => {
        const meta = PHASE_META[phase];
        const reached = events.length > 0;
        const last = index === phases.length - 1;
        const nodeIds = [...new Set(events.flatMap((e) => e.nodeIds))];
        const danger = phase === "failure" || phase === "potential_failure";
        return (
          <li key={phase} className="relative flex gap-3 pb-5 last:pb-0">
            {!last ? (
              <span
                aria-hidden
                className={cn(
                  "absolute top-8 bottom-0 left-[13px] w-px",
                  reached ? "bg-strong" : "border-l border-dashed border-default",
                )}
              />
            ) : null}
            <span
              aria-hidden
              className={cn(
                "relative z-10 flex size-7 shrink-0 items-center justify-center rounded-full border",
                !reached && "border-dashed border-default bg-surface text-muted",
                reached && danger && "border-danger/40 bg-danger-soft text-danger-fg",
                reached && !danger && "border-warning/40 bg-warning-soft text-warning-fg",
              )}
            >
              <meta.Icon className="size-3.5" />
            </span>
            <div className="flex min-w-0 flex-1 flex-col gap-1.5 pt-1">
              <div className="flex flex-wrap items-baseline gap-x-2">
                <h3 className={cn("text-sm font-semibold", reached ? "text-fg" : "text-muted")}>
                  {meta.label}
                </h3>
                {reached ? (
                  <span className="tabular text-xs text-muted">
                    {events.map((e) => formatOffset(e.atSeconds)).join(", ")}
                  </span>
                ) : (
                  <span className="text-xs text-muted">Not reached in this run</span>
                )}
              </div>
              {events.map((e, i) => (
                <p key={`${e.atSeconds}-${i}`} className="text-sm text-fg-secondary">
                  {e.description}
                </p>
              ))}
              {nodeIds.length > 0 ? (
                <ul aria-label={`${meta.label}: components`} className="flex flex-wrap gap-1">
                  {nodeIds.map((id) => (
                    <li
                      key={id}
                      className="rounded-sm border border-default bg-surface-2 px-1.5 py-0.5 text-xs text-fg-secondary"
                    >
                      {nodeName(id)}
                    </li>
                  ))}
                </ul>
              ) : null}
            </div>
          </li>
        );
      })}
    </ol>
  );
}
