import type { NodeInspectorProps } from "./types";

export function EvidenceTab({
  utilization,
  findings,
  security,
  cost,
  onOpenEvidence,
}: Pick<NodeInspectorProps, "utilization" | "findings" | "security" | "cost" | "onOpenEvidence">) {
  const items = new Map<string, string>();
  for (const row of utilization) {
    if (row.evidenceId) items.set(row.evidenceId, `${row.resource} utilization`);
  }
  for (const finding of findings) {
    for (const id of finding.evidenceIds) if (!items.has(id)) items.set(id, finding.title);
  }
  for (const threat of security?.threats ?? []) {
    if (threat.evidenceId && !items.has(threat.evidenceId)) items.set(threat.evidenceId, threat.title);
  }
  if (cost?.evidenceId && !items.has(cost.evidenceId)) items.set(cost.evidenceId, "Cost estimate");
  return (
    <ul className="flex flex-col gap-1.5 rounded-md bg-surface-2 p-2">
      {[...items].map(([id, label]) => (
        <li key={id}>
          <button
            type="button"
            onClick={() => onOpenEvidence(id)}
            className="flex w-full items-center justify-between gap-2 rounded-sm px-2 py-1.5 text-left hover:bg-surface"
          >
            <span className="truncate text-sm text-fg">{label}</span>
            <span className="tabular shrink-0 text-2xs text-muted">{id}</span>
          </button>
        </li>
      ))}
    </ul>
  );
}
