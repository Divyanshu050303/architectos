/**
 * Domain → React Flow edge adapter (spec §10, §69–72). Throughput, critical
 * dependencies, trust boundaries and simulation propagation come from backend
 * analyses; this only selects and formats them.
 */
import type { Edge } from "@xyflow/react";

import type { AnalysisMode, ArchitectureEdge } from "@/types/architecture";
import type { CapacityAnalysis } from "@/types/capacity";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis } from "@/types/security";

import type { SimulationNodeState } from "./simulation-playback";

/** `boundary`: crosses a trust boundary; `propagation`: carries a simulated failure. */
export type EdgeEmphasis = "normal" | "critical" | "boundary" | "propagation" | "dimmed";

export type ArchitectureEdgeData = {
  edge: ArchitectureEdge;
  label: string | null;
  emphasis: EdgeEmphasis;
  /** Overview connections between domains curve instead of routing orthogonally. */
  curve?: "step" | "bezier";
  /** Folded overview connection that runs both ways: arrowheads at both ends. */
  bidirectional?: boolean;
  [key: string]: unknown;
};

export type ArchitectureFlowEdge = Edge<ArchitectureEdgeData, "architecture">;

export interface EdgeOverlayContext {
  mode: AnalysisMode;
  capacity: CapacityAnalysis | null;
  highlightedNodeIds: ReadonlySet<string>;
  selectedEdgeIds?: ReadonlySet<string>;
  reliability?: ReliabilityAnalysis | null;
  security?: SecurityAnalysis | null;
  simulation?: ReadonlyMap<string, SimulationNodeState> | null;
}

const COMPACT_UNITS = [
  { value: 1e9, suffix: "B" },
  { value: 1e6, suffix: "M" },
  { value: 1e3, suffix: "K" },
] as const;

/** 8200 → "8.2K", 950 → "950", 1_500_000 → "1.5M". */
export function formatCompact(value: number): string {
  const abs = Math.abs(value);
  for (const unit of COMPACT_UNITS) {
    if (abs >= unit.value * 0.9995) {
      const scaled = Math.round((value / unit.value) * 10) / 10;
      return `${Number.isInteger(scaled) ? scaled.toFixed(0) : scaled.toFixed(1)}${unit.suffix}`;
    }
  }
  return Math.round(value).toString();
}

/** Trust boundary membership by node id (first boundary wins; boundaries are disjoint by contract). */
export function boundaryIndex(security: SecurityAnalysis): Map<string, { id: string; name: string }> {
  const index = new Map<string, { id: string; name: string }>();
  for (const boundary of security.trustBoundaries) {
    for (const nodeId of boundary.nodeIds) {
      if (!index.has(nodeId)) index.set(nodeId, { id: boundary.id, name: boundary.name });
    }
  }
  return index;
}

export function toFlowEdges(
  edges: readonly ArchitectureEdge[],
  ctx: EdgeOverlayContext,
): ArchitectureFlowEdge[] {
  const rpsByEdge = new Map((ctx.capacity?.edges ?? []).map((e) => [e.edgeId, e.rps]));
  const highlightActive = ctx.highlightedNodeIds.size > 0;
  const criticalEdges =
    ctx.mode === "reliability" && ctx.reliability ? new Set(ctx.reliability.criticalEdgeIds) : null;
  const cascadeEdges =
    ctx.mode === "reliability" && ctx.reliability
      ? new Set(ctx.reliability.cascadeRisks.map((r) => r.edgeId))
      : null;
  const boundaries = ctx.mode === "security" && ctx.security ? boundaryIndex(ctx.security) : null;
  const simulation = ctx.mode === "simulation" ? (ctx.simulation ?? null) : null;

  return edges.map((edge) => {
    let label: string | null = edge.label ?? edge.protocol ?? null;
    let emphasis: EdgeEmphasis = "normal";

    if (ctx.mode === "capacity") {
      const rps = rpsByEdge.get(edge.id);
      if (rps !== undefined) label = `${formatCompact(rps)} RPS`;
    } else if (ctx.mode === "reliability") {
      if (criticalEdges) {
        if (criticalEdges.has(edge.id)) {
          emphasis = "critical";
          label = "Critical dependency";
        } else if (cascadeEdges?.has(edge.id)) {
          label = "Cascade risk";
        }
      } else if (edge.critical && edge.synchronous) {
        // No reliability analysis: fall back to the IR's own critical/synchronous flags.
        emphasis = "critical";
        label = "Critical dependency";
      }
    } else if (boundaries) {
      const from = boundaries.get(edge.source);
      const to = boundaries.get(edge.target);
      if (from && to && from.id !== to.id) {
        emphasis = "boundary";
        label = `→ ${to.name}`;
      }
    } else if (simulation) {
      const source = simulation.get(edge.source) ?? "normal";
      const target = simulation.get(edge.target) ?? "normal";
      if (source !== "normal" && target !== "normal") emphasis = "propagation";
    }

    const touchesHighlight =
      ctx.highlightedNodeIds.has(edge.source) || ctx.highlightedNodeIds.has(edge.target);
    if (highlightActive && !touchesHighlight) emphasis = "dimmed";

    return {
      id: edge.id,
      type: "architecture",
      source: edge.source,
      target: edge.target,
      selected: ctx.selectedEdgeIds?.has(edge.id) ?? false,
      data: { edge, label, emphasis },
    };
  });
}
