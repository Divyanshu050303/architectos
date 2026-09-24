import { useId } from "react";

import type { MigrationStep } from "@/types/evolution";

const NODE_W = 160;
const NODE_H = 44;
const COL_GAP = 40;
const ROW_GAP = 14;
const PAD = 8;
const MAX_TITLE = 19;

interface PlacedStep {
  step: MigrationStep;
  x: number;
  y: number;
}

/** Column = longest dependency chain before the step, so arrows always point right. Layout only. */
function layout(steps: readonly MigrationStep[]): { placed: PlacedStep[]; width: number; height: number } {
  const byId = new Map(steps.map((s) => [s.id, s]));
  const depth = new Map<string, number>();
  const visiting = new Set<string>();
  const depthOf = (step: MigrationStep): number => {
    const known = depth.get(step.id);
    if (known !== undefined) return known;
    if (visiting.has(step.id)) return 0; // malformed cycle: don't recurse forever
    visiting.add(step.id);
    let d = 0;
    for (const id of step.dependsOn) {
      const dep = byId.get(id);
      if (dep) d = Math.max(d, depthOf(dep) + 1);
    }
    visiting.delete(step.id);
    depth.set(step.id, d);
    return d;
  };

  const columns: MigrationStep[][] = [];
  for (const step of [...steps].sort((a, b) => a.order - b.order)) {
    const d = depthOf(step);
    (columns[d] ??= []).push(step);
  }

  const placed: PlacedStep[] = [];
  let rows = 0;
  columns.forEach((column, c) => {
    rows = Math.max(rows, column.length);
    column.forEach((step, r) => {
      placed.push({ step, x: PAD + c * (NODE_W + COL_GAP), y: PAD + r * (NODE_H + ROW_GAP) });
    });
  });
  const width = PAD * 2 + columns.length * NODE_W + Math.max(0, columns.length - 1) * COL_GAP;
  const height = PAD * 2 + rows * NODE_H + Math.max(0, rows - 1) * ROW_GAP;
  return { placed, width, height };
}

function truncate(text: string): string {
  return text.length > MAX_TITLE ? `${text.slice(0, MAX_TITLE - 1)}…` : text;
}

/** Steps as nodes, arrows for `dependsOn`. The SVG is decorative; the list beside it is the accessible form. */
export function MigrationDependencyGraph({ steps }: { steps: readonly MigrationStep[] }) {
  const markerId = `${useId()}-arrow`;
  if (steps.length === 0) return null;

  const { placed, width, height } = layout(steps);
  const at = new Map(placed.map((p) => [p.step.id, p]));
  const orderOf = new Map(steps.map((s) => [s.id, s.order]));
  const roots = placed.filter((p) => p.step.dependsOn.length === 0).map((p) => p.step.order);
  const summary = `Dependency graph of ${steps.length} steps. ${roots.length === 1 ? "Step" : "Steps"} ${roots.join(", ")} can start first.`;

  return (
    <figure className="flex flex-col gap-3">
      <div className="overflow-x-auto" tabIndex={0}>
        <svg
          role="img"
          aria-label={summary}
          width={width}
          height={height}
          viewBox={`0 0 ${width} ${height}`}
          className="block max-w-none"
        >
          <defs>
            <marker
              id={markerId}
              viewBox="0 0 10 10"
              refX="9"
              refY="5"
              markerWidth="7"
              markerHeight="7"
              orient="auto-start-reverse"
            >
              <path d="M 0 0 L 10 5 L 0 10 z" fill="var(--text-muted)" />
            </marker>
          </defs>

          {placed.flatMap(({ step, x, y }) =>
            step.dependsOn.map((depId) => {
              const from = at.get(depId);
              if (!from) return null;
              const x1 = from.x + NODE_W;
              const y1 = from.y + NODE_H / 2;
              const x2 = x - 2;
              const y2 = y + NODE_H / 2;
              const mid = (x1 + x2) / 2;
              return (
                <path
                  key={`${depId}->${step.id}`}
                  d={`M ${x1} ${y1} C ${mid} ${y1}, ${mid} ${y2}, ${x2} ${y2}`}
                  fill="none"
                  stroke="var(--border-strong)"
                  strokeWidth={1.5}
                  markerEnd={`url(#${markerId})`}
                />
              );
            }),
          )}

          {placed.map(({ step, x, y }) => (
            <g key={step.id} transform={`translate(${x} ${y})`}>
              <title>{`Step ${step.order}: ${step.title}`}</title>
              <rect
                width={NODE_W}
                height={NODE_H}
                rx={6}
                fill="var(--surface)"
                stroke={step.status === "done" ? "var(--accent)" : "var(--border-strong)"}
              />
              <circle cx={20} cy={NODE_H / 2} r={11} fill="var(--surface-secondary)" stroke="var(--border)" />
              <text
                x={20}
                y={NODE_H / 2}
                textAnchor="middle"
                dominantBaseline="central"
                fontSize={11}
                fontWeight={600}
                fill="var(--text-primary)"
              >
                {step.order}
              </text>
              <text x={38} y={NODE_H / 2} dominantBaseline="central" fontSize={12} fill="var(--text-primary)">
                {truncate(step.title)}
              </text>
            </g>
          ))}
        </svg>
      </div>
      <figcaption className="text-xs text-fg-secondary">
        Arrows point from a step to the steps that depend on it. Independent chains can run in parallel.
        <ul className="sr-only">
          {[...steps]
            .sort((a, b) => a.order - b.order)
            .map((s) => (
              <li key={s.id}>
                Step {s.order}
                {s.dependsOn.length > 0
                  ? ` depends on step ${s.dependsOn.map((id) => orderOf.get(id) ?? id).join(" and step ")}`
                  : " has no dependencies"}
                .
              </li>
            ))}
        </ul>
      </figcaption>
    </figure>
  );
}
