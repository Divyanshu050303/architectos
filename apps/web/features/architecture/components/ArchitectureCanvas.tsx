"use client";

/**
 * Presentational canvas (spec §22, §58): receives nodes, edges and handlers and knows
 * nothing about the API. React Flow is controlled; positions only change through
 * MOVE_COMPONENTS commands on drag stop, never by mutating architecture state.
 */
import {
  Background,
  BackgroundVariant,
  type EdgeChange,
  type NodeChange,
  type OnConnect,
  type OnNodeDrag,
  ReactFlow,
  SelectionMode,
  useNodesInitialized,
  useReactFlow,
  type XYPosition,
} from "@xyflow/react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import type { Position } from "@/types/architecture";

import type { CanvasFlowEdge, WorkspaceFlowNode } from "../hooks/useArchitectureCanvas";
import { domainFromNodeId } from "../utils/graph-view";
import { ArchitectureEdge, EdgeMarkerDefs } from "./ArchitectureEdge";
import { ArchitectureMiniMap } from "./ArchitectureMiniMap";
import { ArchitectureNode } from "./ArchitectureNode";
import { BoundaryNode } from "./BoundaryNode";
import { DomainNode } from "./DomainNode";

const nodeTypes = { architecture: ArchitectureNode, domain: DomainNode, boundary: BoundaryNode };
const edgeTypes = { architecture: ArchitectureEdge };
const FIT_VIEW_OPTIONS = { padding: 0.2, maxZoom: 1.1 };
const PRO_OPTIONS = { hideAttribution: true };
/** Space + drag pans (default activation key); middle mouse always pans (spec §60). */
const PAN_BUTTONS = [1];
const MULTI_SELECT_KEYS = ["Shift", "Meta", "Control"];
/** Past this size, off-screen nodes are skipped (spec §64–65). */
const VIRTUALIZE_THRESHOLD = 60;

export interface FocusRequest {
  nodeIds: readonly string[];
  token: number;
}

export interface ArchitectureCanvasProps {
  nodes: WorkspaceFlowNode[];
  edges: CanvasFlowEdge[];
  /** False below the lg breakpoint: read-only canvas (spec §63). */
  editable: boolean;
  connecting: boolean;
  showGrid: boolean;
  showMiniMap: boolean;
  focusRequest: FocusRequest | null;
  /** A new value re-fits the view, e.g. after a graph view change. */
  fitToken?: string;
  /** What the re-fit frames; all visible nodes when omitted. */
  fitNodeIds?: readonly string[] | null;
  /** Overview: a collapsed domain was clicked or activated from the keyboard. */
  onDomainActivate?: (domain: string) => void;
  onNodeSelectionChange: (nodeIds: string[]) => void;
  onEdgeSelectionChange: (edgeIds: string[]) => void;
  onNodeClick: (nodeId: string) => void;
  onPaneClick: () => void;
  onConnect: (source: string, target: string) => void;
  onMoveNodes: (positions: Record<string, Position>) => void;
  /** Rendered inside React Flow, e.g. the node context toolbar. */
  children?: React.ReactNode;
}

type Size = { width: number; height: number };

function applySelection(
  current: readonly string[],
  changes: readonly (NodeChange | EdgeChange)[],
): string[] | null {
  let next: Set<string> | null = null;
  for (const change of changes) {
    if (change.type !== "select") continue;
    next ??= new Set(current);
    if (change.selected) next.add(change.id);
    else next.delete(change.id);
  }
  return next ? [...next] : null;
}

/** Focus the viewport on a set of nodes whenever a new request token arrives. */
function FocusController({ request }: { request: FocusRequest | null }) {
  const { fitView } = useReactFlow();
  const initialized = useNodesInitialized();
  const token = request?.token;
  const ids = request?.nodeIds;
  useEffect(() => {
    if (!initialized || !ids || ids.length === 0) return;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    void fitView({
      nodes: ids.map((id) => ({ id })),
      padding: 0.6,
      maxZoom: 1.2,
      duration: reduce ? 0 : 300,
    });
    // Re-run only for a new request token, not for every node update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [token, initialized]);
  return null;
}

/** Fit the visible nodes (or `nodeIds`) whenever the token changes, after the first render. */
function FitController({ token, nodeIds }: { token: string | undefined; nodeIds: readonly string[] | null }) {
  const { fitView, getInternalNode, getNodes } = useReactFlow();
  // The first frame comes from React Flow's own initial fitView.
  const last = useRef(token);
  const targets = useRef(nodeIds);
  useEffect(() => {
    targets.current = nodeIds;
  });
  useEffect(() => {
    if (last.current === token) return;
    last.current = token;
    const reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    const ids = targets.current && targets.current.length > 0 ? targets.current : null;
    let frame = 0;
    let attempts = 0;
    // Newly shown nodes are measured a frame or two after they mount; fit once they are.
    const tryFit = () => {
      const pending = (
        ids ??
        getNodes()
          .filter((n) => !n.hidden)
          .map((n) => n.id)
      ).some((id) => !getInternalNode(id)?.measured.width);
      if (pending && attempts++ < 30) {
        frame = requestAnimationFrame(tryFit);
        return;
      }
      void fitView({
        ...FIT_VIEW_OPTIONS,
        ...(ids ? { nodes: ids.map((id) => ({ id })) } : {}),
        duration: reduce ? 0 : 250,
      });
    };
    frame = requestAnimationFrame(tryFit);
    return () => cancelAnimationFrame(frame);
  }, [token, fitView, getInternalNode, getNodes]);
  return null;
}

export function ArchitectureCanvas({
  nodes,
  edges,
  editable,
  connecting,
  showGrid,
  showMiniMap,
  focusRequest,
  fitToken,
  fitNodeIds = null,
  onDomainActivate,
  onNodeSelectionChange,
  onEdgeSelectionChange,
  onNodeClick,
  onPaneClick,
  onConnect,
  onMoveNodes,
  children,
}: ArchitectureCanvasProps) {
  // Transient view state only: in-flight drag positions and measured sizes.
  const [dragPositions, setDragPositions] = useState<Record<string, XYPosition>>({});
  const [measured, setMeasured] = useState<Record<string, Size>>({});

  const flowNodes = useMemo(
    () =>
      nodes.map((node) => {
        const position = dragPositions[node.id];
        const size = measured[node.id];
        if (!position && !size) return node;
        return { ...node, ...(position ? { position } : {}), ...(size ? { measured: size } : {}) };
      }),
    [nodes, dragPositions, measured],
  );

  // Initial frame: a deep-linked expanded domain is framed on its own components.
  const [initialFit] = useState(() =>
    fitNodeIds && fitNodeIds.length > 0
      ? { ...FIT_VIEW_OPTIONS, nodes: fitNodeIds.map((id) => ({ id })) }
      : FIT_VIEW_OPTIONS,
  );

  const selectedNodeIds = useMemo(() => nodes.filter((n) => n.selected).map((n) => n.id), [nodes]);
  const selectedEdgeIds = useMemo(() => edges.filter((e) => e.selected).map((e) => e.id), [edges]);

  const handleNodesChange = useCallback(
    (changes: NodeChange<WorkspaceFlowNode>[]) => {
      let drag: Record<string, XYPosition> | null = null;
      let sizes: Record<string, Size> | null = null;
      for (const change of changes) {
        if (change.type === "position" && change.position && change.dragging) {
          (drag ??= {})[change.id] = change.position;
        } else if (change.type === "dimensions" && change.dimensions) {
          (sizes ??= {})[change.id] = change.dimensions;
        }
      }
      if (drag) setDragPositions((current) => ({ ...current, ...drag }));
      if (sizes) setMeasured((current) => ({ ...current, ...sizes }));
      const selection = applySelection(selectedNodeIds, changes);
      if (selection) {
        // Domains are not components: selecting one (click or keyboard) expands it instead.
        const domain = selection.map(domainFromNodeId).find((d) => d !== null);
        if (domain) onDomainActivate?.(domain);
        onNodeSelectionChange(selection.filter((id) => domainFromNodeId(id) === null));
      }
    },
    [selectedNodeIds, onNodeSelectionChange, onDomainActivate],
  );

  const handleEdgesChange = useCallback(
    (changes: EdgeChange<CanvasFlowEdge>[]) => {
      const selection = applySelection(selectedEdgeIds, changes);
      if (selection) onEdgeSelectionChange(selection);
    },
    [selectedEdgeIds, onEdgeSelectionChange],
  );

  const commitDrag = useCallback(
    (dragged: readonly WorkspaceFlowNode[]) => {
      const original = new Map(nodes.map((n) => [n.id, n.position]));
      const positions: Record<string, Position> = {};
      for (const node of dragged) {
        const before = original.get(node.id);
        const x = Math.round(node.position.x);
        const y = Math.round(node.position.y);
        if (before && (before.x !== x || before.y !== y)) positions[node.id] = { x, y };
      }
      if (Object.keys(positions).length > 0) onMoveNodes(positions);
      setDragPositions({});
    },
    [nodes, onMoveNodes],
  );
  const handleDragStop = useCallback<OnNodeDrag<WorkspaceFlowNode>>(
    (_event, _node, dragged) => commitDrag(dragged),
    [commitDrag],
  );
  const handleSelectionDragStop = useCallback(
    (_event: React.MouseEvent, dragged: WorkspaceFlowNode[]) => commitDrag(dragged),
    [commitDrag],
  );

  const handleConnect = useCallback<OnConnect>(
    (connection) => {
      if (connection.source && connection.target && connection.source !== connection.target) {
        onConnect(connection.source, connection.target);
      }
    },
    [onConnect],
  );

  const handleNodeClick = useCallback(
    (_event: React.MouseEvent, node: WorkspaceFlowNode) => {
      if (node.type === "domain") onDomainActivate?.(node.data.domain);
      else if (node.type === "architecture") onNodeClick(node.id);
    },
    [onNodeClick, onDomainActivate],
  );

  return (
    <div
      className={cn("architecture-canvas relative size-full", connecting && "is-connecting")}
      data-connecting={connecting || undefined}
    >
      <EdgeMarkerDefs />
      <ReactFlow<WorkspaceFlowNode, CanvasFlowEdge>
        nodes={flowNodes}
        edges={edges}
        nodeTypes={nodeTypes}
        edgeTypes={edgeTypes}
        onNodesChange={handleNodesChange}
        onEdgesChange={handleEdgesChange}
        onNodeDragStop={handleDragStop}
        onSelectionDragStop={handleSelectionDragStop}
        onConnect={handleConnect}
        onNodeClick={handleNodeClick}
        onPaneClick={onPaneClick}
        nodesDraggable={editable}
        nodesConnectable={editable}
        elementsSelectable
        selectionOnDrag={editable}
        selectionMode={SelectionMode.Partial}
        panOnDrag={editable ? PAN_BUTTONS : true}
        panOnScroll={editable}
        zoomOnDoubleClick={false}
        multiSelectionKeyCode={MULTI_SELECT_KEYS}
        deleteKeyCode={null}
        minZoom={0.2}
        maxZoom={2}
        fitView
        fitViewOptions={initialFit}
        onlyRenderVisibleElements={nodes.length > VIRTUALIZE_THRESHOLD}
        proOptions={PRO_OPTIONS}
        aria-label="Architecture canvas"
      >
        {showGrid ? (
          <Background variant={BackgroundVariant.Dots} gap={20} size={1.2} color="var(--canvas-grid)" />
        ) : null}
        {showMiniMap ? <ArchitectureMiniMap /> : null}
        <FocusController request={focusRequest} />
        <FitController token={fitToken} nodeIds={fitNodeIds} />
        {children}
      </ReactFlow>
    </div>
  );
}
