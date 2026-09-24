/**
 * Auto layouts (spec §66). Pure: each returns new top-left positions only; the caller
 * applies them as a MOVE_COMPONENTS layout command, never a semantic change (spec §67).
 *
 *   hierarchical  dagre, top-down or left-right
 *   force         deterministic force simulation (seeded, fixed iterations) + overlap removal
 *   domain        dagre per domain, domains arranged in a grid
 */
import dagre from "@dagrejs/dagre";

import type { ArchitectureEdge, ArchitectureNode, Position } from "@/types/architecture";

import { NODE_HEIGHT, NODE_WIDTH } from "../constants";

export type LayoutDirection = "TB" | "LR";

export interface LayoutOptions {
  direction: LayoutDirection;
  nodeSeparation?: number;
  rankSeparation?: number;
}

/** Every option of the "Auto layout" menu. */
export type AutoLayoutKind = "hierarchical-tb" | "hierarchical-lr" | "force" | "domain";

export const AUTO_LAYOUT_LABELS: Record<AutoLayoutKind, string> = {
  "hierarchical-tb": "Hierarchical · top-down",
  "hierarchical-lr": "Hierarchical · left-right",
  force: "Force-directed",
  domain: "Domain grouped",
};

type LayoutNode = Pick<ArchitectureNode, "id">;
type LayoutEdge = Pick<ArchitectureEdge, "source" | "target">;

export interface SizedLayoutNode {
  id: string;
  width: number;
  height: number;
}

const NODE_SEPARATION = 48;
const RANK_SEPARATION = 96;

/** dagre with per-node sizes; top-left positions keyed by id. */
export function layoutSized(
  nodes: readonly SizedLayoutNode[],
  edges: readonly LayoutEdge[],
  { direction, nodeSeparation = NODE_SEPARATION, rankSeparation = RANK_SEPARATION }: LayoutOptions,
): Record<string, Position> {
  const graph = new dagre.graphlib.Graph();
  graph.setGraph({ rankdir: direction, nodesep: nodeSeparation, ranksep: rankSeparation });
  graph.setDefaultEdgeLabel(() => ({}));

  const sizes = new Map(nodes.map((n) => [n.id, n]));
  for (const node of sizes.values()) graph.setNode(node.id, { width: node.width, height: node.height });
  for (const edge of edges) {
    if (sizes.has(edge.source) && sizes.has(edge.target) && edge.source !== edge.target) {
      graph.setEdge(edge.source, edge.target);
    }
  }

  dagre.layout(graph);

  const positions: Record<string, Position> = {};
  for (const node of sizes.values()) {
    const { x, y } = graph.node(node.id);
    positions[node.id] = { x: Math.round(x - node.width / 2), y: Math.round(y - node.height / 2) };
  }
  return positions;
}

/** Top-left positions keyed by node id (React Flow and the IR use top-left origins). */
export function layoutGraph(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  options: LayoutOptions,
): Record<string, Position> {
  return layoutSized(
    nodes.map((n) => ({ id: n.id, width: NODE_WIDTH, height: NODE_HEIGHT })),
    edges,
    options,
  );
}

// --- Force-directed ----------------------------------------------------------

export interface ForceLayoutOptions {
  /** Same seed + same graph → same layout. */
  seed?: number;
  iterations?: number;
}

/** Minimum clear space kept between two nodes after overlap removal. */
export const LAYOUT_GAP = 32;
const IDEAL_EDGE_LENGTH = 300;
const MAX_OVERLAP_PASSES = 400;

/** mulberry32: tiny deterministic PRNG. */
function seededRandom(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

/** Push overlapping node boxes apart along the axis of least overlap until none overlap. */
function removeOverlaps(xs: Float64Array, ys: Float64Array, width: number, height: number, gap: number) {
  const n = xs.length;
  const minDx = width + gap;
  const minDy = height + gap;
  for (let pass = 0; pass < MAX_OVERLAP_PASSES; pass++) {
    let moved = false;
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        const dx = (xs[j] ?? 0) - (xs[i] ?? 0);
        const dy = (ys[j] ?? 0) - (ys[i] ?? 0);
        const overlapX = minDx - Math.abs(dx);
        const overlapY = minDy - Math.abs(dy);
        if (overlapX <= 0 || overlapY <= 0) continue;
        moved = true;
        // Separate along the cheaper axis; ties broken by index so the result is deterministic.
        if (overlapX / minDx < overlapY / minDy) {
          const shift = (overlapX / 2) * (dx < 0 ? -1 : 1);
          xs[i] = (xs[i] ?? 0) - shift;
          xs[j] = (xs[j] ?? 0) + shift;
        } else {
          const shift = (overlapY / 2) * (dy < 0 ? -1 : 1);
          ys[i] = (ys[i] ?? 0) - shift;
          ys[j] = (ys[j] ?? 0) + shift;
        }
      }
    }
    if (!moved) return;
  }
}

/** Fruchterman–Reingold with a fixed iteration count and a seeded start, then overlap removal. */
export function layoutForceDirected(
  nodes: readonly LayoutNode[],
  edges: readonly LayoutEdge[],
  { seed = 7, iterations = 300 }: ForceLayoutOptions = {},
): Record<string, Position> {
  const ids = [...new Set(nodes.map((n) => n.id))];
  const n = ids.length;
  if (n === 0) return {};
  const index = new Map(ids.map((id, i) => [id, i]));
  const links: [number, number][] = [];
  for (const edge of edges) {
    const s = index.get(edge.source);
    const t = index.get(edge.target);
    if (s !== undefined && t !== undefined && s !== t) links.push([s, t]);
  }

  const random = seededRandom(seed);
  const k = IDEAL_EDGE_LENGTH;
  const radius = k * Math.sqrt(n) * 0.5;
  const xs = new Float64Array(n);
  const ys = new Float64Array(n);
  for (let i = 0; i < n; i++) {
    const angle = (2 * Math.PI * i) / n;
    xs[i] = Math.cos(angle) * radius + (random() - 0.5) * k * 0.1;
    ys[i] = Math.sin(angle) * radius + (random() - 0.5) * k * 0.1;
  }

  const dispX = new Float64Array(n);
  const dispY = new Float64Array(n);
  let temperature = radius * 0.5;
  const cooling = temperature / (iterations + 1);

  for (let iter = 0; iter < iterations; iter++) {
    dispX.fill(0);
    dispY.fill(0);
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = (xs[i] ?? 0) - (xs[j] ?? 0);
        let dy = (ys[i] ?? 0) - (ys[j] ?? 0);
        if (dx === 0 && dy === 0) {
          dx = 0.01 * (i + 1);
          dy = 0.01 * (j + 1);
        }
        const dist2 = dx * dx + dy * dy;
        const force = (k * k) / dist2;
        dispX[i] = (dispX[i] ?? 0) + dx * force;
        dispY[i] = (dispY[i] ?? 0) + dy * force;
        dispX[j] = (dispX[j] ?? 0) - dx * force;
        dispY[j] = (dispY[j] ?? 0) - dy * force;
      }
    }
    for (const [s, t] of links) {
      const dx = (xs[s] ?? 0) - (xs[t] ?? 0);
      const dy = (ys[s] ?? 0) - (ys[t] ?? 0);
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = dist / k;
      dispX[s] = (dispX[s] ?? 0) - dx * force;
      dispY[s] = (dispY[s] ?? 0) - dy * force;
      dispX[t] = (dispX[t] ?? 0) + dx * force;
      dispY[t] = (dispY[t] ?? 0) + dy * force;
    }
    for (let i = 0; i < n; i++) {
      const dx = dispX[i] ?? 0;
      const dy = dispY[i] ?? 0;
      const len = Math.sqrt(dx * dx + dy * dy);
      if (len === 0) continue;
      const step = Math.min(len, temperature);
      xs[i] = (xs[i] ?? 0) + (dx / len) * step;
      ys[i] = (ys[i] ?? 0) + (dy / len) * step;
    }
    temperature = Math.max(temperature - cooling, 1);
  }

  // Wide, short cards: stretch horizontally before separating so rows read naturally.
  for (let i = 0; i < n; i++) xs[i] = (xs[i] ?? 0) * (NODE_WIDTH / NODE_HEIGHT) * 0.6;
  removeOverlaps(xs, ys, NODE_WIDTH, NODE_HEIGHT, LAYOUT_GAP);

  let minX = Number.POSITIVE_INFINITY;
  let minY = Number.POSITIVE_INFINITY;
  for (let i = 0; i < n; i++) {
    minX = Math.min(minX, xs[i] ?? 0);
    minY = Math.min(minY, ys[i] ?? 0);
  }
  const positions: Record<string, Position> = {};
  ids.forEach((id, i) => {
    positions[id] = { x: Math.round((xs[i] ?? 0) - minX), y: Math.round((ys[i] ?? 0) - minY) };
  });
  return positions;
}

// --- Domain grouped ----------------------------------------------------------

/** Components without a domain are grouped here. */
export const UNGROUPED_DOMAIN = "other";
const DOMAIN_GAP = 160;

export function domainOf(node: Pick<ArchitectureNode, "domain">): string {
  return node.domain?.trim() || UNGROUPED_DOMAIN;
}

/** Domains in first-appearance order with their member ids. */
export function groupByDomain(
  nodes: readonly Pick<ArchitectureNode, "id" | "domain">[],
): Map<string, string[]> {
  const groups = new Map<string, string[]>();
  for (const node of nodes) {
    const domain = domainOf(node);
    const members = groups.get(domain);
    if (members) members.push(node.id);
    else groups.set(domain, [node.id]);
  }
  return groups;
}

/** dagre inside each domain, then domain blocks packed row by row in a near-square grid. */
export function layoutByDomain(
  nodes: readonly Pick<ArchitectureNode, "id" | "domain">[],
  edges: readonly LayoutEdge[],
  { direction }: LayoutOptions = { direction: "TB" },
): Record<string, Position> {
  const groups = groupByDomain(nodes);
  const blocks = [...groups].map(([domain, ids]) => {
    const members = new Set(ids);
    const local = layoutGraph(
      ids.map((id) => ({ id })),
      edges.filter((e) => members.has(e.source) && members.has(e.target)),
      { direction },
    );
    let width = 0;
    let height = 0;
    for (const p of Object.values(local)) {
      width = Math.max(width, p.x + NODE_WIDTH);
      height = Math.max(height, p.y + NODE_HEIGHT);
    }
    return { domain, local, width, height };
  });

  const columns = Math.max(1, Math.ceil(Math.sqrt(blocks.length)));
  const positions: Record<string, Position> = {};
  let y = 0;
  for (let row = 0; row * columns < blocks.length; row++) {
    const rowBlocks = blocks.slice(row * columns, row * columns + columns);
    let x = 0;
    let rowHeight = 0;
    for (const block of rowBlocks) {
      for (const [id, p] of Object.entries(block.local)) positions[id] = { x: p.x + x, y: p.y + y };
      x += block.width + DOMAIN_GAP;
      rowHeight = Math.max(rowHeight, block.height);
    }
    y += rowHeight + DOMAIN_GAP;
  }
  return positions;
}

/** Dispatch for the "Auto layout" menu. */
export function autoLayout(
  kind: AutoLayoutKind,
  nodes: readonly Pick<ArchitectureNode, "id" | "domain">[],
  edges: readonly LayoutEdge[],
): Record<string, Position> {
  switch (kind) {
    case "hierarchical-tb":
      return layoutGraph(nodes, edges, { direction: "TB" });
    case "hierarchical-lr":
      return layoutGraph(nodes, edges, { direction: "LR" });
    case "force":
      return layoutForceDirected(nodes, edges);
    case "domain":
      return layoutByDomain(nodes, edges, { direction: "TB" });
  }
}

/** Handle orientation that reads best for each layout. */
export function layoutDirectionOf(kind: AutoLayoutKind): LayoutDirection {
  return kind === "hierarchical-lr" ? "LR" : "TB";
}
