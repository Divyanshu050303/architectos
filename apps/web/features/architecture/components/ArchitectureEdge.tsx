"use client";

import { BaseEdge, EdgeLabelRenderer, type EdgeProps, getBezierPath, getSmoothStepPath } from "@xyflow/react";
import { memo } from "react";

import { cn } from "@/lib/utils";

import type { CanvasFlowEdge } from "../hooks/useArchitectureCanvas";

/** SVG marker ids defined once by ArchitectureCanvas. */
export const EDGE_MARKERS = {
  normal: "arch-arrow",
  critical: "arch-arrow-critical",
  accent: "arch-arrow-accent",
} as const;

/** Arrowheads for edges; CSS variables keep them in step with the theme. */
export function EdgeMarkerDefs() {
  const markers = [
    { id: EDGE_MARKERS.normal, fill: "var(--border-control)" },
    { id: EDGE_MARKERS.critical, fill: "var(--danger)" },
    { id: EDGE_MARKERS.accent, fill: "var(--accent-strong)" },
  ];
  return (
    <svg aria-hidden className="pointer-events-none absolute size-0 overflow-hidden">
      <defs>
        {markers.map((m) => (
          <marker
            key={m.id}
            id={m.id}
            viewBox="0 0 10 10"
            refX="9"
            refY="5"
            markerWidth="7"
            markerHeight="7"
            orient="auto-start-reverse"
          >
            <path d="M0,1 L9,5 L0,9 z" style={{ fill: m.fill }} />
          </marker>
        ))}
      </defs>
    </svg>
  );
}

function ArchitectureEdgeComponent({
  id,
  sourceX,
  sourceY,
  targetX,
  targetY,
  sourcePosition,
  targetPosition,
  data,
  selected,
}: EdgeProps<CanvasFlowEdge>) {
  const geometry = { sourceX, sourceY, targetX, targetY, sourcePosition, targetPosition };
  const [path, labelX, labelY] =
    data?.curve === "bezier" ? getBezierPath(geometry) : getSmoothStepPath({ ...geometry, borderRadius: 8 });

  const emphasis = data?.emphasis ?? "normal";
  const preview = data?.preview ?? null;
  const label = data?.label ?? null;

  const style =
    preview === "added"
      ? { stroke: "var(--accent-strong)", strokeDasharray: "5 4" }
      : preview === "removed"
        ? { stroke: "var(--danger)", strokeDasharray: "5 4", opacity: 0.6 }
        : undefined;
  const marker =
    preview === "added"
      ? EDGE_MARKERS.accent
      : emphasis === "critical" || emphasis === "propagation" || preview === "removed"
        ? EDGE_MARKERS.critical
        : selected
          ? EDGE_MARKERS.accent
          : EDGE_MARKERS.normal;

  return (
    <>
      <BaseEdge
        id={id}
        path={path}
        markerEnd={`url(#${marker})`}
        markerStart={data?.bidirectional ? `url(#${marker})` : undefined}
        style={style}
        interactionWidth={16}
      />
      {label ? (
        <EdgeLabelRenderer>
          <div
            data-emphasis={emphasis}
            style={{ transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)` }}
            className={cn(
              "nodrag nopan pointer-events-none absolute rounded-full border bg-surface px-1.5 text-2xs leading-4",
              "tabular whitespace-nowrap",
              emphasis === "critical"
                ? "border-danger/40 text-danger-fg"
                : emphasis === "boundary"
                  ? "border-info/40 text-info-fg"
                  : preview === "added"
                    ? "border-accent/40 text-accent-fg"
                    : "border-default text-fg-secondary",
              emphasis === "dimmed" && "opacity-40",
              preview === "removed" && "line-through opacity-60",
            )}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      ) : null}
    </>
  );
}

export const ArchitectureEdge = memo(ArchitectureEdgeComponent);
