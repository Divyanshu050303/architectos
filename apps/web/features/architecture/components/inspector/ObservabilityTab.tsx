import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Meter } from "@/components/ui/progress";
import { formatPercent } from "@/lib/formatting";
import { cn } from "@/lib/utils";

import { COVERAGE_SIGNALS } from "../../utils/node-transform";
import { Fact } from "./shared";
import type { NodeObservabilityDetails } from "./types";

export function ObservabilityTab({ observability }: { observability: NodeObservabilityDetails }) {
  const { coverage, slos, gap } = observability;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">From the observability analysis</span>
        <ProvenanceTag kind="calculated" />
      </div>
      {coverage ? (
        <section className="flex flex-col gap-1.5">
          <h3 className="label-caps">Coverage</h3>
          <dl className="divide-y divide-default">
            {[...COVERAGE_SIGNALS, { key: "D", signal: "dashboards" as const, label: "Dashboards" }].map(
              (row) => (
                <Fact key={row.signal} label={row.label}>
                  {coverage[row.signal] ? (
                    <span className="text-accent-fg">Covered</span>
                  ) : (
                    <span className="font-medium text-warning-fg">Missing</span>
                  )}
                </Fact>
              ),
            )}
          </dl>
        </section>
      ) : null}
      {slos.length > 0 ? (
        <section className="flex flex-col gap-2">
          <h3 className="label-caps">SLOs</h3>
          <ul className="flex flex-col gap-3">
            {slos.map((slo) => {
              const meeting = slo.current >= slo.target;
              return (
                <li key={slo.id} className="flex flex-col gap-1">
                  <div className="flex items-baseline justify-between gap-2 text-sm">
                    <span className="text-fg">{slo.name}</span>
                    <span className="tabular shrink-0 text-xs text-muted">{slo.window}</span>
                  </div>
                  <div className="flex justify-between text-xs">
                    <span className="text-fg-secondary">
                      Target <span className="tabular text-fg">{formatPercent(slo.target, 2)}</span> · Current{" "}
                      <span className={cn("tabular", meeting ? "text-fg" : "font-medium text-danger-fg")}>
                        {formatPercent(slo.current, 2)}
                      </span>
                    </span>
                  </div>
                  <Meter
                    value={slo.errorBudgetRemaining}
                    tone={
                      slo.errorBudgetRemaining < 0.25
                        ? "danger"
                        : slo.errorBudgetRemaining < 0.5
                          ? "warning"
                          : "accent"
                    }
                    label={`${slo.name} error budget remaining ${formatPercent(slo.errorBudgetRemaining)}`}
                  />
                  <span className="text-2xs text-muted">
                    <span className="tabular">{formatPercent(slo.errorBudgetRemaining)}</span> error budget
                    left
                  </span>
                </li>
              );
            })}
          </ul>
        </section>
      ) : null}
      {gap && gap.missing.length > 0 ? (
        <section className="flex flex-col gap-1.5 rounded-md border border-warning/40 bg-warning-soft p-2.5">
          <h3 className="label-caps">Gaps</h3>
          <p className="text-xs text-fg">
            Missing <span className="font-medium">{gap.missing.join(", ")}</span>
          </p>
          <p className="text-xs text-fg-secondary">{gap.recommendation}</p>
        </section>
      ) : null}
    </div>
  );
}
