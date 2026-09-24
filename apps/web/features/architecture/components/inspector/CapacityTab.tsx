import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { StatusBadge } from "@/components/ui/badge";
import { Meter, type MeterTone } from "@/components/ui/progress";
import { formatCompact, formatNumber, formatPercent } from "@/lib/formatting";
import type { ComponentUtilization } from "@/types/capacity";

import { WhyButton } from "./shared";
import type { NodeInspectorProps } from "./types";

const UTIL_TONE: Record<ComponentUtilization["status"], MeterTone> = {
  healthy: "accent",
  warning: "warning",
  critical: "danger",
};

export function CapacityTab({
  utilization,
  onOpenEvidence,
}: Pick<NodeInspectorProps, "utilization" | "onOpenEvidence">) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">From the capacity engine</span>
        <ProvenanceTag kind="calculated" />
      </div>
      <ul className="flex flex-col gap-3">
        {utilization.map((row) => (
          <li key={row.resource} className="flex flex-col gap-1.5">
            <div className="flex items-baseline justify-between gap-2 text-sm">
              <span className="text-fg">{row.resource}</span>
              <span className="tabular text-fg">
                {formatCompact(row.used)} / {formatCompact(row.limit)}
                <span className="text-muted"> {row.unit}</span>
              </span>
            </div>
            <Meter
              value={row.utilization}
              threshold={row.threshold}
              tone={UTIL_TONE[row.status]}
              label={`${row.resource} utilization ${formatPercent(row.utilization)}`}
            />
            <div className="flex items-center justify-between gap-2">
              <StatusBadge status={row.status} />
              <span className="flex items-center gap-1 text-xs text-muted">
                <span className="tabular">{formatPercent(row.utilization)}</span> used
                {row.evidenceId ? (
                  <WhyButton evidenceId={row.evidenceId} label={row.resource} onOpen={onOpenEvidence} />
                ) : null}
              </span>
            </div>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ConstraintsTab({ utilization }: Pick<NodeInspectorProps, "utilization">) {
  return (
    <div className="flex flex-col gap-3">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">Limits and warning thresholds</span>
        <ProvenanceTag kind="calculated" />
      </div>
      <dl className="divide-y divide-default">
        {utilization.map((row) => (
          <div key={row.resource} className="flex flex-col gap-0.5 py-2">
            <dt className="text-sm font-medium text-fg">{row.resource}</dt>
            <dd className="flex justify-between text-xs text-fg-secondary">
              <span>Configured limit</span>
              <span className="tabular text-fg">
                {formatNumber(row.limit)} {row.unit}
              </span>
            </dd>
            <dd className="flex justify-between text-xs text-fg-secondary">
              <span>Warning threshold</span>
              <span className="tabular text-fg">{formatPercent(row.threshold)}</span>
            </dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
