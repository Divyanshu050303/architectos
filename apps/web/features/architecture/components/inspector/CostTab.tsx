import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { formatCurrency } from "@/lib/formatting";

import { Fact, WhyButton } from "./shared";
import type { NodeCostDetails, NodeInspectorProps } from "./types";

export function CostTab({
  cost,
  onOpenEvidence,
}: { cost: NodeCostDetails } & Pick<NodeInspectorProps, "onOpenEvidence">) {
  const money = (value: number) => formatCurrency(value, cost.currency);
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <span className="text-xs text-muted">From the cost engine</span>
        <ProvenanceTag kind="calculated" />
      </div>
      <div className="flex items-baseline justify-between gap-2">
        <span className="text-sm text-fg-secondary">Monthly</span>
        <span className="flex items-baseline gap-2">
          <span className="tabular text-lg font-semibold text-fg">{money(cost.cost.monthly)}/mo</span>
          {cost.evidenceId ? (
            <WhyButton evidenceId={cost.evidenceId} label="cost estimate" onOpen={onOpenEvidence} />
          ) : null}
        </span>
      </div>
      {cost.cost.breakdown.length > 0 ? (
        <section className="flex flex-col gap-1.5">
          <h3 className="label-caps">Breakdown</h3>
          <dl className="divide-y divide-default">
            {cost.cost.breakdown.map((line) => (
              <Fact key={line.item} label={line.item}>
                <span className="tabular">{money(line.monthly)}</span>
              </Fact>
            ))}
          </dl>
        </section>
      ) : null}
    </div>
  );
}
