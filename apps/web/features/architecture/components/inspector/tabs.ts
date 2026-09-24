/** Which inspector tabs a component has: a tab appears only when there is data behind it (spec §29). */
import type { NodeInspectorProps } from "./types";

export type InspectorTab =
  | "overview"
  | "configuration"
  | "capacity"
  | "constraints"
  | "failure-modes"
  | "security"
  | "observability"
  | "cost"
  | "evidence";

export const TAB_LABELS: Record<InspectorTab, string> = {
  overview: "Overview",
  configuration: "Configuration",
  capacity: "Capacity",
  constraints: "Constraints",
  "failure-modes": "Failure modes",
  security: "Security",
  observability: "Observability",
  cost: "Cost",
  evidence: "Evidence",
};

export function availableTabs({
  node,
  utilization,
  findings,
  security,
  observability,
  cost,
}: Pick<
  NodeInspectorProps,
  "node" | "utilization" | "findings" | "security" | "observability" | "cost"
>): InspectorTab[] {
  const tabs: InspectorTab[] = ["overview"];
  if (Object.keys(node.configuration).length > 0) tabs.push("configuration");
  if (utilization.length > 0) tabs.push("capacity", "constraints");
  if (findings.length > 0) tabs.push("failure-modes");
  if (security && (security.exposure || security.controls || security.threats.length > 0))
    tabs.push("security");
  if (observability && (observability.coverage || observability.slos.length > 0 || observability.gap)) {
    tabs.push("observability");
  }
  if (cost) tabs.push("cost");
  const hasEvidence =
    utilization.some((row) => row.evidenceId !== null) ||
    findings.some((f) => f.evidenceIds.length > 0) ||
    (security?.threats.some((t) => t.evidenceId !== null) ?? false) ||
    Boolean(cost?.evidenceId);
  if (hasEvidence) tabs.push("evidence");
  return tabs;
}
