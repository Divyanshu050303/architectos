/**
 * Canvas wiring for the workspace (spec §64–65): clicks while connecting, pane clicks,
 * drag-to-connect, domain expansion in the overview and the viewport framing tokens.
 */
import { useCallback, useEffect, useMemo } from "react";

import { useWorkspaceStore } from "@/stores/workspace-store";
import type { Architecture } from "@/types/architecture";

import type { FocusRequest } from "../components/ArchitectureCanvas";
import { domainOf } from "../utils/graph-layout";
import type { GraphView } from "../utils/graph-view";

export interface CanvasInteractionsInput {
  present: Architecture | null;
  view: GraphView;
  expandedDomain: string | null;
  setDomain: (domain: string | null) => void;
  clearHighlight: () => void;
  connect: (source: string, target: string) => unknown;
  selectedNodeIds: readonly string[];
  focusNodeId: string | null;
  focusHops: number;
  viewingVersion: number | null;
  highlightedNodeIds: string[];
  highlightToken: number;
}

export function useCanvasInteractions({
  present,
  view,
  expandedDomain,
  setDomain,
  clearHighlight,
  connect,
  selectedNodeIds,
  focusNodeId,
  focusHops,
  viewingVersion,
  highlightedNodeIds,
  highlightToken,
}: CanvasInteractionsInput) {
  const focusRequest = useMemo<FocusRequest | null>(
    () => (highlightedNodeIds.length > 0 ? { nodeIds: highlightedNodeIds, token: highlightToken } : null),
    [highlightedNodeIds, highlightToken],
  );

  // --- Large graph views (spec §65) -------------------------------------------------
  const fitToken =
    view === "overview"
      ? `overview:${expandedDomain ?? ""}`
      : view === "focused"
        ? `focused:${focusNodeId ?? ""}:${focusHops}`
        : `detailed:${viewingVersion ?? ""}`;

  // An expanded domain is framed on its own components rather than the whole overview.
  const fitNodeIds = useMemo(
    () =>
      view === "overview" && expandedDomain && present
        ? present.nodes.filter((n) => domainOf(n) === expandedDomain).map((n) => n.id)
        : null,
    [view, expandedDomain, present],
  );

  const activateDomain = useCallback(
    (domain: string) => {
      useWorkspaceStore.getState().clearSelection();
      setDomain(domain);
    },
    [setDomain],
  );

  // Selecting a component hidden inside a collapsed domain (e.g. ?node= or the inspector's
  // neighbour list) expands its domain.
  const selectedId = selectedNodeIds.length === 1 ? selectedNodeIds[0] : undefined;
  useEffect(() => {
    if (view !== "overview" || !selectedId || !present) return;
    const node = present.nodes.find((n) => n.id === selectedId);
    if (node && domainOf(node) !== expandedDomain) setDomain(domainOf(node));
  }, [view, selectedId, present, expandedDomain, setDomain]);

  const onNodeClick = useCallback(
    (nodeId: string) => {
      const store = useWorkspaceStore.getState();
      const source = store.connectSourceId;
      if (!source) return;
      store.cancelConnect();
      if (source !== nodeId) connect(source, nodeId);
    },
    [connect],
  );

  const onPaneClick = useCallback(() => {
    const store = useWorkspaceStore.getState();
    store.cancelConnect();
    if (store.highlightedNodeIds.length > 0) clearHighlight();
  }, [clearHighlight]);

  const onConnect = useCallback((source: string, target: string) => void connect(source, target), [connect]);

  return { focusRequest, fitToken, fitNodeIds, activateDomain, onNodeClick, onPaneClick, onConnect };
}
