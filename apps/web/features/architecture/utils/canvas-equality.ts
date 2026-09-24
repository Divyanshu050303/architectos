/**
 * Field-wise equality for canvas elements (spec §64). Node and edge objects are
 * rebuilt on every overlay pass; these let memoised components and the stabiliser
 * in useArchitectureCanvas skip work when nothing visible changed.
 */
import type { CoverageChip, NodeBadge } from "./node-transform";

interface ComparableNodeData {
  node: unknown;
  status: string;
  statusLabel: string;
  metric: { label: string; value: string } | null;
  utilization: number | null;
  highlighted: boolean;
  dimmed: boolean;
  mode: string;
  preview: string | null;
  direction: string;
  category: string;
  badges: readonly NodeBadge[];
  coverage?: readonly CoverageChip[] | null;
  simulation?: string | null;
  versionChanged?: boolean;
}

function sameBadges(a: readonly NodeBadge[], b: readonly NodeBadge[]): boolean {
  return (
    a.length === b.length && a.every((badge, i) => badge.label === b[i]?.label && badge.tone === b[i]?.tone)
  );
}

function sameCoverage(
  a: readonly CoverageChip[] | null | undefined,
  b: readonly CoverageChip[] | null | undefined,
): boolean {
  if (!a || !b) return (a ?? null) === (b ?? null);
  return (
    a.length === b.length && a.every((chip, i) => chip.key === b[i]?.key && chip.present === b[i]?.present)
  );
}

export function sameNodeData(a: ComparableNodeData, b: ComparableNodeData): boolean {
  return (
    a.node === b.node &&
    a.status === b.status &&
    a.statusLabel === b.statusLabel &&
    a.metric?.label === b.metric?.label &&
    a.metric?.value === b.metric?.value &&
    a.utilization === b.utilization &&
    a.highlighted === b.highlighted &&
    a.dimmed === b.dimmed &&
    a.mode === b.mode &&
    a.preview === b.preview &&
    a.direction === b.direction &&
    a.category === b.category &&
    (a.simulation ?? null) === (b.simulation ?? null) &&
    (a.versionChanged ?? false) === (b.versionChanged ?? false) &&
    sameBadges(a.badges, b.badges) &&
    sameCoverage(a.coverage, b.coverage)
  );
}

/** Shallow equality of plain data records (domain and boundary nodes, edge data). */
export function sameRecord(a: Record<string, unknown>, b: Record<string, unknown>): boolean {
  if (a === b) return true;
  const keys = Object.keys(a);
  if (keys.length !== Object.keys(b).length) return false;
  return keys.every((key) => Object.is(a[key], b[key]));
}
