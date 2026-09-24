/**
 * Large graph strategy (spec §64–65): Overview collapses components into their
 * domains, Focused shows one component and its neighbourhood, Detailed shows
 * everything. Pure view derivations: the semantic architecture is never changed,
 * and overview positions are a view-only layout (spec §67).
 */
import type { ArchitectureEdge, ArchitectureNode, Position } from "@/types/architecture";

import { NODE_HEIGHT, NODE_WIDTH } from "../constants";
import { domainOf, groupByDomain, layoutSized, UNGROUPED_DOMAIN } from "./graph-layout";

export const GRAPH_VIEWS = ["overview", "detailed", "focused"] as const;
export type GraphView = (typeof GRAPH_VIEWS)[number];

export const GRAPH_VIEW_LABELS: Record<GraphView, string> = {
  overview: "Overview",
  detailed: "Detailed",
  focused: "Focused",
};

/** Above this many components the workspace opens in Overview (spec §65). */
export const OVERVIEW_THRESHOLD = 60;

export const DOMAIN_NODE_PREFIX = "domain:";
export const OVERVIEW_DIRECTION = "LR" as const;
export const DOMAIN_NODE_WIDTH = 272;
export const DOMAIN_NODE_HEIGHT = 124;

export function defaultGraphView(nodeCount: number, hasDomains: boolean): GraphView {
  return hasDomains && nodeCount > OVERVIEW_THRESHOLD ? "overview" : "detailed";
}

export function hasDomains(nodes: readonly Pick<ArchitectureNode, "domain">[]): boolean {
  return nodes.some((n) => Boolean(n.domain?.trim()));
}

export function domainNodeId(domain: string): string {
  return `${DOMAIN_NODE_PREFIX}${domain}`;
}

export function isDomainNodeId(id: string): boolean {
  return id.startsWith(DOMAIN_NODE_PREFIX);
}

export function domainFromNodeId(id: string): string | null {
  return isDomainNodeId(id) ? id.slice(DOMAIN_NODE_PREFIX.length) : null;
}

/** "payments" → "Payments", "order_fulfillment" → "Order fulfillment". */
export function domainLabel(domain: string): string {
  if (domain === UNGROUPED_DOMAIN) return "Other";
  const spaced = domain.replace(/[_-]+/g, " ").trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1);
}

export interface DomainGroup {
  /** Canvas id, `domain:<domain>`. */
  id: string;
  domain: string;
  label: string;
  nodeIds: string[];
}

export interface AggregatedEdge {
  id: string;
  source: string;
  target: string;
  /** Real connections folded into this one, in both directions. */
  edgeIds: string[];
  /** Connections run both ways between the pair. */
  bidirectional: boolean;
}

export interface CollapsedGraph {
  /** Domains shown as a single node. */
  domains: DomainGroup[];
  /** Members of collapsed domains: hidden on the canvas. */
  hiddenNodeIds: Set<string>;
  /** Connections that touch a collapsed domain, folded per visible endpoint pair. */
  aggregatedEdges: AggregatedEdge[];
  /** Real connections hidden because at least one end is collapsed. */
  hiddenEdgeIds: Set<string>;
  /** View-only positions for domain nodes and the expanded domain's members. */
  positions: Record<string, Position>;
}

/**
 * Collapse every domain except `expanded` into one node, fold the connections between
 * them, and lay the result out top-down.
 */
export function collapseByDomain(
  nodes: readonly Pick<ArchitectureNode, "id" | "domain">[],
  edges: readonly Pick<ArchitectureEdge, "id" | "source" | "target">[],
  expanded: string | null,
): CollapsedGraph {
  const groups = groupByDomain(nodes);
  const visibleId = new Map<string, string>();
  const domains: DomainGroup[] = [];
  const hiddenNodeIds = new Set<string>();

  for (const [domain, ids] of groups) {
    if (domain === expanded) {
      for (const id of ids) visibleId.set(id, id);
      continue;
    }
    const id = domainNodeId(domain);
    domains.push({ id, domain, label: domainLabel(domain), nodeIds: ids });
    for (const nodeId of ids) {
      visibleId.set(nodeId, id);
      hiddenNodeIds.add(nodeId);
    }
  }

  const folded = new Map<string, AggregatedEdge>();
  const hiddenEdgeIds = new Set<string>();
  for (const edge of edges) {
    const source = visibleId.get(edge.source);
    const target = visibleId.get(edge.target);
    if (!source || !target) continue;
    if (source === edge.source && target === edge.target) continue;
    hiddenEdgeIds.add(edge.id);
    if (source === target) continue;
    // One line per pair of visible endpoints, whichever way the connections run.
    const key = source < target ? `${source}<>${target}` : `${target}<>${source}`;
    const existing = folded.get(key);
    if (existing) {
      existing.edgeIds.push(edge.id);
      if (existing.source !== source) existing.bidirectional = true;
    } else {
      folded.set(key, {
        id: `agg:${source}->${target}`,
        source,
        target,
        edgeIds: [edge.id],
        bidirectional: false,
      });
    }
  }
  const aggregatedEdges = [...folded.values()];

  const layoutNodes = [
    ...domains.map((d) => ({ id: d.id, width: DOMAIN_NODE_WIDTH, height: DOMAIN_NODE_HEIGHT })),
    ...(expanded ? (groups.get(expanded) ?? []) : []).map((id) => ({
      id,
      width: NODE_WIDTH,
      height: NODE_HEIGHT,
    })),
  ];
  const layoutEdges = [
    ...aggregatedEdges,
    ...edges.filter((e) => !hiddenEdgeIds.has(e.id) && visibleId.get(e.source) === e.source),
  ];
  // Left to right: the canvas is wider than tall, so the overview fits at a readable zoom.
  const positions = layoutSized(layoutNodes, layoutEdges, {
    direction: OVERVIEW_DIRECTION,
    nodeSeparation: 36,
    rankSeparation: 88,
  });

  return { domains, hiddenNodeIds, aggregatedEdges, hiddenEdgeIds, positions };
}

/** The domain a component belongs to, as used by the overview. */
export function nodeDomain(
  nodes: readonly Pick<ArchitectureNode, "id" | "domain">[],
  id: string,
): string | null {
  const node = nodes.find((n) => n.id === id);
  return node ? domainOf(node) : null;
}

/** `nodeId` plus every component within `hops` connections, ignoring direction. */
export function neighbourhood(
  edges: readonly Pick<ArchitectureEdge, "source" | "target">[],
  nodeId: string,
  hops: number,
): Set<string> {
  const adjacency = new Map<string, string[]>();
  const link = (from: string, to: string) => {
    const list = adjacency.get(from);
    if (list) list.push(to);
    else adjacency.set(from, [to]);
  };
  for (const edge of edges) {
    if (edge.source === edge.target) continue;
    link(edge.source, edge.target);
    link(edge.target, edge.source);
  }
  const seen = new Set([nodeId]);
  let frontier = [nodeId];
  for (let hop = 0; hop < hops && frontier.length > 0; hop++) {
    const next: string[] = [];
    for (const id of frontier) {
      for (const neighbour of adjacency.get(id) ?? []) {
        if (!seen.has(neighbour)) {
          seen.add(neighbour);
          next.push(neighbour);
        }
      }
    }
    frontier = next;
  }
  return seen;
}
