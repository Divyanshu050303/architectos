import { StatusBadge } from "@/components/ui/badge";
import { Meter, type MeterTone } from "@/components/ui/progress";
import { Table, Td, Th } from "@/components/ui/table";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { formatNumber, formatPercent } from "@/lib/formatting";
import type { ComponentUtilization } from "@/types/capacity";

const TONE: Record<ComponentUtilization["status"], MeterTone> = {
  healthy: "accent",
  warning: "warning",
  critical: "danger",
};

export interface UtilizationListProps {
  utilization: readonly ComponentUtilization[];
  /** Display name for a component id. */
  nodeName: (nodeId: string) => string;
}

function groupByNode(rows: readonly ComponentUtilization[]): Array<[string, ComponentUtilization[]]> {
  const groups = new Map<string, ComponentUtilization[]>();
  for (const row of rows) {
    const group = groups.get(row.nodeId);
    if (group) group.push(row);
    else groups.set(row.nodeId, [row]);
  }
  return [...groups.entries()];
}

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

/** Per-component resource utilization as reported by the capacity engine (spec §35). */
export function UtilizationList({ utilization, nodeName }: UtilizationListProps) {
  if (utilization.length === 0) {
    return <p className="text-sm text-muted">The capacity engine reported no component resources.</p>;
  }

  return (
    <Table aria-label="System utilization by component">
      <thead>
        <tr>
          <Th>Resource</Th>
          <Th className="text-right">Used / limit</Th>
          <Th className="w-[28%] min-w-32">Utilization</Th>
          <Th>Status</Th>
          <Th>
            <span className="sr-only">Evidence</span>
          </Th>
        </tr>
      </thead>
      {groupByNode(utilization).map(([nodeId, rows]) => {
        const name = nodeName(nodeId);
        return (
          <tbody key={nodeId}>
            <tr>
              <th
                scope="rowgroup"
                colSpan={5}
                className="border-b border-default bg-surface-2 px-3 py-1.5 text-left text-xs font-semibold text-fg"
              >
                {name}
              </th>
            </tr>
            {rows.map((row) => (
              <tr key={`${row.nodeId}-${row.resource}`}>
                <Td className="text-fg-secondary">{capitalize(row.resource)}</Td>
                <Td className="tabular text-right text-xs whitespace-nowrap">
                  {formatNumber(row.used)} / {formatNumber(row.limit)}
                  <span className="ml-1 text-muted">{row.unit}</span>
                </Td>
                <Td>
                  <div className="flex items-center gap-2">
                    <Meter
                      value={row.utilization}
                      threshold={row.threshold}
                      tone={TONE[row.status]}
                      label={`${name} ${row.resource} utilization, warning threshold ${formatPercent(row.threshold)}`}
                      className="flex-1"
                    />
                    <span className="tabular w-10 text-right text-xs text-fg">
                      {formatPercent(row.utilization)}
                    </span>
                  </div>
                </Td>
                <Td>
                  <StatusBadge status={row.status} />
                </Td>
                <Td className="text-right">
                  <WhyButton evidenceId={row.evidenceId} subject={`${name} ${row.resource}`} />
                </Td>
              </tr>
            ))}
          </tbody>
        );
      })}
    </Table>
  );
}
