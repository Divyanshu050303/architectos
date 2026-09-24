/**
 * Derives React Flow nodes/edges from the draft architecture, the backend analysis
 * overlays, the URL analysis mode and graph view, and an open AI proposal (preview
 * only). React Flow types stay inside features/architecture (spec §10).
 */
import { type Edge, type Node, useReactFlow, useStoreApi } from "@xyflow/react";
import { useMemo, useState } from "react";
import { useShallow } from "zustand/react/shallow";

import { useArchitectureStore } from "@/stores/architecture-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type {
  AnalysisMode,
  Architecture,
  ArchitectureEdge,
  ArchitectureNode,
  Position,
  Proposal,
} from "@/types/architecture";
import type { CapacityAnalysis } from "@/types/capacity";
import type { ComponentType } from "@/types/component";
import type { CostEstimate } from "@/types/cost";
import type { ObservabilityAnalysis } from "@/types/observability";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis } from "@/types/security";
import type { Finding } from "@/types/validation";

import { COMPONENT_TYPE_META, NODE_HEIGHT, NODE_WIDTH } from "../constants";
import { sameNodeData, sameRecord } from "../utils/canvas-equality";
import { type ArchitectureEdgeData, type EdgeEmphasis, toFlowEdges } from "../utils/edge-transform";
import type { LayoutDirection } from "../utils/graph-layout";
import { collapseByDomain, type GraphView, neighbourhood, OVERVIEW_DIRECTION } from "../utils/graph-view";
import {
  type AnalyzedStatus,
  type ArchitectureNodeData,
  STATUS_LABELS,
  toFlowNodes,
  worstStatus,
} from "../utils/node-transform";
import { boundaryRects, type LabelAnchor } from "../utils/overlay-geometry";
import type { SimulationNodeState } from "../utils/simulation-playback";

/** How a node/edge appears in an AI proposal preview. Never mutates the draft. */
export type PreviewKind = "added" | "removed" | "updated";

export type CanvasNodeData = ArchitectureNodeData & {
  preview: PreviewKind | null;
  direction: LayoutDirection;
};
export type CanvasFlowNode = Node<CanvasNodeData, "architecture">;

/** A collapsed domain in the overview (spec §65). */
export type DomainNodeData = {
  domain: string;
  label: string;
  count: number;
  status: AnalyzedStatus;
  statusLabel: string;
  /** "8 services · 3 databases" */
  typeSummary: string;
  mode: AnalysisMode;
  [key: string]: unknown;
};
export type DomainFlowNode = Node<DomainNodeData, "domain">;

/** Trust boundary backdrop in the security overlay; visual only. */
export type BoundaryNodeData = { name: string; labelAnchor: LabelAnchor; [key: string]: unknown };
export type BoundaryFlowNode = Node<BoundaryNodeData, "boundary">;

export type WorkspaceFlowNode = CanvasFlowNode | DomainFlowNode | BoundaryFlowNode;

export type CanvasEdgeData = ArchitectureEdgeData & { preview: PreviewKind | null };
export type CanvasFlowEdge = Edge<CanvasEdgeData, "architecture">;

export interface ProposalPreview {
  addedNodes: ArchitectureNode[];
  removedNodeIds: ReadonlySet<string>;
  updatedNodeIds: ReadonlySet<string>;
  addedEdges: ArchitectureEdge[];
  removedEdgeIds: ReadonlySet<string>;
}

const EMPTY_SET: ReadonlySet<string> = new Set();

/** Only pending "change" proposals are previewed on the canvas. */
export function buildProposalPreview(proposal: Proposal | null | undefined): ProposalPreview | null {
  if (!proposal || proposal.kind !== "change" || proposal.status !== "pending") return null;
  const preview = {
    addedNodes: [] as ArchitectureNode[],
    removedNodeIds: new Set<string>(),
    updatedNodeIds: new Set<string>(),
    addedEdges: [] as ArchitectureEdge[],
    removedEdgeIds: new Set<string>(),
  };
  for (const change of proposal.changes) {
    switch (change.op) {
      case "add_node":
        preview.addedNodes.push(change.node);
        break;
      case "remove_node":
        preview.removedNodeIds.add(change.nodeId);
        break;
      case "update_node":
        preview.updatedNodeIds.add(change.nodeId);
        break;
      case "add_edge":
        preview.addedEdges.push(change.edge);
        break;
      case "remove_edge":
        preview.removedEdgeIds.add(change.edgeId);
        break;
    }
  }
  return preview;
}

/** Screen-reader summary of a node (spec §62): name, category, status and metric. */
export function describeNode(data: CanvasNodeData): string {
  const parts = [data.node.name, data.category];
  if (data.node.technology) parts.push(data.node.technology);
  parts.push(`status ${data.statusLabel}`);
  if (data.metric) parts.push(`${data.metric.label} ${data.metric.value}`.trim());
  for (const badge of data.badges) parts.push(badge.label.replace(/^⚠\s*/, ""));
  const missing = data.coverage?.filter((c) => !c.present).map((c) => c.label.toLowerCase()) ?? [];
  if (missing.length > 0) parts.push(`missing ${missing.join(", ")}`);
  if (data.preview === "added") parts.push("proposed addition");
  if (data.preview === "removed") parts.push("proposed removal");
  if (data.preview === "updated") parts.push("proposed change");
  return parts.join(", ");
}

export function describeDomain(data: DomainNodeData): string {
  return `${data.label} domain, ${data.count} components, status ${data.statusLabel}. Activate to expand.`;
}

function edgeData(edge: Edge<ArchitectureEdgeData>, preview: PreviewKind | null): CanvasEdgeData {
  // toFlowEdges always sets data; Edge only types it as optional.
  if (!edge.data) throw new Error(`Edge "${edge.id}" has no data`);
  return { ...edge.data, preview };
}

const EMPHASIS_RANK: Record<EdgeEmphasis, number> = {
  dimmed: 0,
  normal: 1,
  boundary: 2,
  propagation: 3,
  critical: 4,
};

const ANALYZED: ReadonlySet<string> = new Set(["default", "healthy", "warning", "critical"]);

function typeSummary(nodes: readonly ArchitectureNode[]): string {
  const counts = new Map<ComponentType, number>();
  for (const node of nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  return [...counts]
    .sort((a, b) => b[1] - a[1])
    .slice(0, 3)
    .map(([type, count]) => `${count} ${COMPONENT_TYPE_META[type].category.toLowerCase()}`)
    .join(" · ");
}

// --- Identity stabiliser -------------------------------------------------------

function sameNode(a: WorkspaceFlowNode, b: WorkspaceFlowNode): boolean {
  if (
    a.id !== b.id ||
    a.type !== b.type ||
    a.position.x !== b.position.x ||
    a.position.y !== b.position.y ||
    a.selected !== b.selected ||
    a.hidden !== b.hidden ||
    a.draggable !== b.draggable ||
    a.connectable !== b.connectable ||
    a.ariaLabel !== b.ariaLabel ||
    a.width !== b.width ||
    a.height !== b.height
  ) {
    return false;
  }
  if (a.type === "architecture" && b.type === "architecture") return sameNodeData(a.data, b.data);
  return sameRecord(a.data, b.data);
}

function sameEdge(a: CanvasFlowEdge, b: CanvasFlowEdge): boolean {
  return (
    a.id === b.id &&
    a.source === b.source &&
    a.target === b.target &&
    a.selected === b.selected &&
    a.hidden === b.hidden &&
    a.data?.edge.id === b.data?.edge.id &&
    a.data?.label === b.data?.label &&
    a.data?.emphasis === b.data?.emphasis &&
    a.data?.curve === b.data?.curve &&
    a.data?.bidirectional === b.data?.bidirectional &&
    a.data?.preview === b.data?.preview
  );
}

/**
 * Reuse the previous object for every element whose visible fields did not change, so
 * React Flow skips re-adopting unchanged nodes on selection or overlay changes (spec §64).
 * The cache lives in a closure (not a ref) and is only touched from a memo, so repeated
 * renders with the same input return the same array.
 */
function createStabilizer<T extends { id: string }>(equal: (a: T, b: T) => boolean) {
  let previousList: T[] = [];
  let previousById = new Map<string, T>();
  return (next: T[]): T[] => {
    let changed = next.length !== previousList.length;
    const byId = new Map<string, T>();
    const list = next.map((item, i) => {
      const old = previousById.get(item.id);
      const kept = old && equal(old, item) ? old : item;
      if (kept !== previousList[i]) changed = true;
      byId.set(item.id, kept);
      return kept;
    });
    if (changed) previousList = list;
    previousById = byId;
    return previousList;
  };
}

function useStable<T extends { id: string }>(next: T[], equal: (a: T, b: T) => boolean): T[] {
  const [stabilize] = useState(() => createStabilizer(equal));
  return useMemo(() => stabilize(next), [stabilize, next]);
}

// --- Hook ------------------------------------------------------------------------

export interface ArchitectureCanvasInput {
  mode: AnalysisMode;
  capacity: CapacityAnalysis | null;
  findings: readonly Finding[];
  proposal: Proposal | null | undefined;
  editable: boolean;
  reliability?: ReliabilityAnalysis | null;
  security?: SecurityAnalysis | null;
  cost?: CostEstimate | null;
  observability?: ObservabilityAnalysis | null;
  /** Node states at the current simulation playback step. */
  simulation?: ReadonlyMap<string, SimulationNodeState> | null;
  /** Nodes in the simulation run while playback is running. */
  simulationActive?: ReadonlySet<string> | null;
  /** The active overlay's analysis for the shown version is still loading. */
  loading?: boolean;
  /** Nodes changed by the version that just loaded (version transition). */
  versionChangedNodeIds?: ReadonlySet<string> | null;
  view?: GraphView;
  /** Overview: the one domain shown expanded. */
  expandedDomain?: string | null;
  /** Focused: the component in focus and how far its neighbourhood reaches. */
  focusNodeId?: string | null;
  focusHops?: number;
  /** Shown instead of the draft, e.g. a read-only historical version. */
  architecture?: Architecture | null;
}

export function useArchitectureCanvas({
  mode,
  capacity,
  findings,
  proposal,
  editable,
  reliability = null,
  security = null,
  cost = null,
  observability = null,
  simulation = null,
  simulationActive = null,
  loading = false,
  versionChangedNodeIds = null,
  view = "detailed",
  expandedDomain = null,
  focusNodeId = null,
  focusHops = 1,
  architecture = null,
}: ArchitectureCanvasInput) {
  const draft = useArchitectureStore((s) => s.present);
  const present = architecture ?? draft;
  const { selectedNodeIds, selectedEdgeIds, highlightedNodeIds, direction } = useWorkspaceStore(
    useShallow((s) => ({
      selectedNodeIds: s.selectedNodeIds,
      selectedEdgeIds: s.selectedEdgeIds,
      highlightedNodeIds: s.highlightedNodeIds,
      direction: s.layoutDirection,
    })),
  );

  const preview = useMemo(() => buildProposalPreview(proposal), [proposal]);

  // Structure-only derivations: recomputed on edits, not on selection or overlay changes.
  const presentNodes = present?.nodes;
  const presentEdges = present?.edges;
  const collapsed = useMemo(
    () =>
      view === "overview" && presentNodes && presentEdges
        ? collapseByDomain(presentNodes, presentEdges, expandedDomain)
        : null,
    [view, presentNodes, presentEdges, expandedDomain],
  );
  const focusSet = useMemo(
    () =>
      view === "focused" && focusNodeId && presentEdges && presentNodes?.some((n) => n.id === focusNodeId)
        ? neighbourhood(presentEdges, focusNodeId, focusHops)
        : null,
    [view, focusNodeId, focusHops, presentEdges, presentNodes],
  );

  const rawNodes = useMemo<WorkspaceFlowNode[]>(() => {
    if (!present) return [];
    const ctx = {
      mode,
      capacity,
      findings,
      highlightedNodeIds: new Set(highlightedNodeIds),
      selectedNodeIds: new Set(selectedNodeIds),
      reliability,
      security,
      cost,
      observability,
      simulation,
      simulationActive,
      loading,
      versionChangedNodeIds,
    };
    const removed = preview?.removedNodeIds ?? EMPTY_SET;
    const updated = preview?.updatedNodeIds ?? EMPTY_SET;
    const draggable = editable && view !== "overview";

    const real: CanvasFlowNode[] = toFlowNodes(present.nodes, ctx).map((n) => {
      const data: CanvasNodeData = {
        ...n.data,
        preview: removed.has(n.id) ? "removed" : updated.has(n.id) ? "updated" : null,
        // The overview has its own layout, so handles follow it.
        direction: collapsed ? OVERVIEW_DIRECTION : direction,
      };
      const hidden = collapsed ? collapsed.hiddenNodeIds.has(n.id) : focusSet ? !focusSet.has(n.id) : false;
      const position = collapsed?.positions[n.id] ?? n.position;
      return {
        ...n,
        position,
        data,
        hidden,
        draggable,
        connectable: editable,
        ariaLabel: describeNode(data),
      };
    });

    const result: WorkspaceFlowNode[] = [];

    // Trust boundaries sit behind everything (security overlay, detailed/focused views).
    if (mode === "security" && security && !collapsed) {
      const positions = new Map(real.filter((n) => !n.hidden).map((n) => [n.id, n.position]));
      for (const rect of boundaryRects(security.trustBoundaries, positions)) {
        result.push({
          id: `boundary:${rect.id}`,
          type: "boundary",
          position: { x: rect.x, y: rect.y },
          width: rect.width,
          height: rect.height,
          data: { name: rect.name, labelAnchor: rect.labelAnchor },
          zIndex: -1,
          draggable: false,
          selectable: false,
          connectable: false,
          focusable: false,
          ariaLabel: `Trust boundary: ${rect.name}`,
        });
      }
    }

    result.push(...real);

    if (collapsed) {
      const statusById = new Map(real.map((n) => [n.id, n.data.status]));
      const byId = new Map(present.nodes.map((n) => [n.id, n]));
      for (const group of collapsed.domains) {
        let status: AnalyzedStatus = "default";
        for (const id of group.nodeIds) {
          const s = statusById.get(id);
          if (s && ANALYZED.has(s)) status = worstStatus(status, s as AnalyzedStatus);
        }
        const members = group.nodeIds.flatMap((id) => byId.get(id) ?? []);
        const data: DomainNodeData = {
          domain: group.domain,
          label: group.label,
          count: group.nodeIds.length,
          status,
          statusLabel: STATUS_LABELS[status],
          typeSummary: typeSummary(members),
          mode,
        };
        result.push({
          id: group.id,
          type: "domain",
          position: collapsed.positions[group.id] ?? { x: 0, y: 0 },
          data,
          draggable: false,
          connectable: false,
          selected: false,
          ariaLabel: describeDomain(data),
        });
      }
    }

    if (!preview) return result;
    const existing = new Set(present.nodes.map((n) => n.id));
    const ghostCtx = {
      ...ctx,
      capacity: null,
      findings: [],
      selectedNodeIds: EMPTY_SET,
      loading: false,
      versionChangedNodeIds: null,
    };
    const ghosts: CanvasFlowNode[] = toFlowNodes(
      preview.addedNodes.filter((n) => !existing.has(n.id)),
      ghostCtx,
    ).map((n) => {
      const data: CanvasNodeData = { ...n.data, preview: "added", direction, dimmed: false };
      return {
        ...n,
        data,
        // Proposed components have no place in the domain overview.
        hidden: Boolean(collapsed),
        draggable: false,
        selectable: false,
        connectable: false,
        focusable: false,
        ariaLabel: describeNode(data),
      };
    });
    return [...result, ...ghosts];
  }, [
    present,
    mode,
    capacity,
    findings,
    highlightedNodeIds,
    selectedNodeIds,
    preview,
    direction,
    editable,
    reliability,
    security,
    cost,
    observability,
    simulation,
    simulationActive,
    loading,
    versionChangedNodeIds,
    collapsed,
    focusSet,
    view,
  ]);

  const rawEdges = useMemo<CanvasFlowEdge[]>(() => {
    if (!present) return [];
    const highlighted = new Set(highlightedNodeIds);
    const removedNodes = preview?.removedNodeIds ?? EMPTY_SET;
    const removedEdges = preview?.removedEdgeIds ?? EMPTY_SET;

    const real: CanvasFlowEdge[] = toFlowEdges(present.edges, {
      mode,
      capacity,
      highlightedNodeIds: highlighted,
      selectedEdgeIds: new Set(selectedEdgeIds),
      reliability,
      security,
      simulation,
    }).map((e) => {
      const isRemoved = removedEdges.has(e.id) || removedNodes.has(e.source) || removedNodes.has(e.target);
      const data = edgeData(e, isRemoved ? "removed" : null);
      const hidden = collapsed
        ? collapsed.hiddenEdgeIds.has(e.id)
        : focusSet
          ? !focusSet.has(e.source) || !focusSet.has(e.target)
          : false;
      return {
        ...e,
        data,
        hidden,
        focusable: true,
        domAttributes: { "data-emphasis": data.emphasis } as CanvasFlowEdge["domAttributes"],
      };
    });

    if (collapsed) {
      const byId = new Map(real.map((e) => [e.id, e]));
      for (const agg of collapsed.aggregatedEdges) {
        let emphasis: EdgeEmphasis = "normal";
        for (const id of agg.edgeIds) {
          const member = byId.get(id)?.data?.emphasis;
          if (member && EMPHASIS_RANK[member] > EMPHASIS_RANK[emphasis]) emphasis = member;
        }
        // Only the count: per-connection protocols would clutter the overview.
        const label = agg.edgeIds.length > 1 ? `${agg.edgeIds.length} connections` : null;
        real.push({
          id: agg.id,
          type: "architecture",
          source: agg.source,
          target: agg.target,
          selectable: false,
          focusable: false,
          data: {
            edge: { id: agg.id, source: agg.source, target: agg.target, synchronous: true, critical: false },
            label,
            emphasis,
            curve: "bezier",
            bidirectional: agg.bidirectional,
            preview: null,
          },
          domAttributes: { "data-emphasis": emphasis } as CanvasFlowEdge["domAttributes"],
        });
      }
    }

    if (!preview || preview.addedEdges.length === 0) return real;
    const existing = new Set(present.edges.map((e) => e.id));
    const ghosts: CanvasFlowEdge[] = toFlowEdges(
      preview.addedEdges.filter((e) => !existing.has(e.id)),
      { mode: "topology", capacity: null, highlightedNodeIds: EMPTY_SET },
    ).map((e) => ({
      ...e,
      data: edgeData(e, "added"),
      hidden: Boolean(collapsed),
      selectable: false,
      focusable: false,
    }));
    return [...real, ...ghosts];
  }, [
    present,
    mode,
    capacity,
    highlightedNodeIds,
    selectedEdgeIds,
    preview,
    reliability,
    security,
    simulation,
    collapsed,
    focusSet,
  ]);

  const nodes = useStable(rawNodes, sameNode);
  const edges = useStable(rawEdges, sameEdge);

  return { nodes, edges, preview };
}

function prefersReducedMotion(): boolean {
  return typeof window !== "undefined" && window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Viewport controls as plain functions, so the toolbar and keyboard shortcuts never
 * touch React Flow types. Must be used under ReactFlowProvider.
 */
export function useCanvasControls() {
  const flow = useReactFlow();
  const store = useStoreApi();
  return useMemo(() => {
    const duration = () => (prefersReducedMotion() ? 0 : 200);
    return {
      zoomIn: () => void flow.zoomIn({ duration: duration() }),
      zoomOut: () => void flow.zoomOut({ duration: duration() }),
      fitView: () => void flow.fitView({ padding: 0.2, duration: duration() }),
      fitNodes: (nodeIds: readonly string[]) =>
        void flow.fitView({
          nodes: nodeIds.map((id) => ({ id })),
          padding: 0.6,
          maxZoom: 1.2,
          duration: duration(),
        }),
      /** Top-left position that centres a new node in the visible canvas. */
      viewportCenter: (): Position => {
        const dom = store.getState().domNode;
        if (!dom) return { x: 0, y: 0 };
        const rect = dom.getBoundingClientRect();
        const center = flow.screenToFlowPosition({
          x: rect.left + rect.width / 2,
          y: rect.top + rect.height / 2,
        });
        return { x: Math.round(center.x - NODE_WIDTH / 2), y: Math.round(center.y - NODE_HEIGHT / 2) };
      },
    };
  }, [flow, store]);
}

export type CanvasControls = ReturnType<typeof useCanvasControls>;
