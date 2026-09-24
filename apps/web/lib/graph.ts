/** Domain graph helpers shared across features. No React Flow here (spec §10). */
import type { Architecture, ArchitectureEdge } from "@/types/architecture";

type Graph = Pick<Architecture, "nodes" | "edges">;

/** Connections that start or end at `nodeId`. */
export function edgesOf(architecture: Graph, nodeId: string): ArchitectureEdge[] {
  return architecture.edges.filter((e) => e.source === nodeId || e.target === nodeId);
}

/** Ids of components directly connected to `nodeId`, split by direction. */
export function neighbors(
  architecture: Graph,
  nodeId: string,
): { upstream: string[]; downstream: string[]; all: string[] } {
  const upstream = new Set<string>();
  const downstream = new Set<string>();
  for (const edge of architecture.edges) {
    if (edge.target === nodeId && edge.source !== nodeId) upstream.add(edge.source);
    if (edge.source === nodeId && edge.target !== nodeId) downstream.add(edge.target);
  }
  return {
    upstream: [...upstream],
    downstream: [...downstream],
    all: [...new Set([...upstream, ...downstream])],
  };
}

/** Display name for a component id; falls back to the id for unknown components. */
export function nodeName(architecture: Graph, id: string): string {
  return architecture.nodes.find((n) => n.id === id)?.name ?? id;
}

/** "API → Payment Service" */
export function describeEdge(architecture: Graph, edge: Pick<ArchitectureEdge, "source" | "target">): string {
  return `${nodeName(architecture, edge.source)} → ${nodeName(architecture, edge.target)}`;
}
