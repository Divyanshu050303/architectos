import { ArrowDownRight, ArrowUpRight } from "lucide-react";

import { formatPercent } from "@/lib/formatting";
import { neighbors, nodeName } from "@/lib/graph";
import type { ComponentUtilization } from "@/types/capacity";

import { COMPONENT_TYPE_META } from "../../constants";
import { Fact } from "./shared";
import type { NodeInspectorProps } from "./types";

function NeighbourList({
  ids,
  direction,
  architecture,
  onSelectNode,
}: {
  ids: string[];
  direction: "in" | "out";
} & Pick<NodeInspectorProps, "architecture" | "onSelectNode">) {
  if (ids.length === 0) return <p className="text-xs text-muted">None</p>;
  return (
    <ul className="flex flex-col">
      {ids.map((id) => (
        <li key={id}>
          <button
            type="button"
            onClick={() => onSelectNode(id)}
            className="flex w-full items-center gap-1.5 rounded-sm px-1.5 py-1 text-left text-sm text-fg hover:bg-surface-2"
          >
            {direction === "in" ? (
              <ArrowDownRight aria-hidden className="size-3.5 text-muted" />
            ) : (
              <ArrowUpRight aria-hidden className="size-3.5 text-muted" />
            )}
            {nodeName(architecture, id)}
          </button>
        </li>
      ))}
    </ul>
  );
}

export function OverviewTab({
  node,
  architecture,
  utilization,
  findings,
  onSelectNode,
}: Pick<NodeInspectorProps, "node" | "architecture" | "utilization" | "findings" | "onSelectNode">) {
  const { upstream, downstream } = neighbors(architecture, node.id);
  const peak = utilization.reduce<ComponentUtilization | null>(
    (worst, row) => (!worst || row.utilization > worst.utilization ? row : worst),
    null,
  );
  const replicas = node.configuration.replicas;

  return (
    <div className="flex flex-col gap-4">
      <dl className="divide-y divide-default">
        <Fact label="Type">{COMPONENT_TYPE_META[node.type].category}</Fact>
        <Fact label="Technology">{node.technology || "—"}</Fact>
        {node.domain ? <Fact label="Domain">{node.domain}</Fact> : null}
        {typeof replicas === "number" ? (
          <Fact label="Replicas">
            <span className="tabular">{replicas}</span>
          </Fact>
        ) : null}
        {peak ? (
          <Fact label="Peak utilization">
            <span className="tabular">
              {formatPercent(peak.utilization)} {peak.resource}
            </span>
          </Fact>
        ) : null}
        <Fact label="Open findings">
          <span className="tabular">{findings.length}</span>
        </Fact>
      </dl>
      <section className="flex flex-col gap-1.5">
        <h3 className="label-caps">Called by</h3>
        <NeighbourList
          ids={upstream}
          direction="in"
          architecture={architecture}
          onSelectNode={onSelectNode}
        />
      </section>
      <section className="flex flex-col gap-1.5">
        <h3 className="label-caps">Depends on</h3>
        <NeighbourList
          ids={downstream}
          direction="out"
          architecture={architecture}
          onSelectNode={onSelectNode}
        />
      </section>
    </div>
  );
}
