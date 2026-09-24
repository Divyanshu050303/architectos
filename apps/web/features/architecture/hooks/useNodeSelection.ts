/** Canvas selection, kept in the workspace store so the inspector and URL can follow it. */
import { useCallback } from "react";
import { useShallow } from "zustand/react/shallow";

import { track } from "@/lib/analytics";
import { useWorkspaceStore } from "@/stores/workspace-store";

function sameIds(a: readonly string[], b: readonly string[]): boolean {
  return a.length === b.length && a.every((id, i) => id === b[i]);
}

export function useNodeSelection(projectId: string) {
  const { selectedNodeIds, selectedEdgeIds } = useWorkspaceStore(
    useShallow((s) => ({ selectedNodeIds: s.selectedNodeIds, selectedEdgeIds: s.selectedEdgeIds })),
  );

  /** React Flow reports node and edge selection separately; each updates only its half. */
  const setNodeSelection = useCallback(
    (nodeIds: string[]) => {
      const ws = useWorkspaceStore.getState();
      if (sameIds(nodeIds, ws.selectedNodeIds)) return;
      ws.select({ nodeIds, edgeIds: ws.selectedEdgeIds });
      if (nodeIds.length === 1) {
        ws.openInspector();
        track("node_selected", { projectId });
      }
    },
    [projectId],
  );

  const setEdgeSelection = useCallback((edgeIds: string[]) => {
    const ws = useWorkspaceStore.getState();
    if (sameIds(edgeIds, ws.selectedEdgeIds)) return;
    ws.select({ nodeIds: ws.selectedNodeIds, edgeIds });
    if (edgeIds.length === 1 && ws.selectedNodeIds.length === 0) ws.openInspector();
  }, []);

  /** Select exactly one component, e.g. from the inspector's neighbour list. */
  const selectNode = useCallback(
    (nodeId: string) => {
      const ws = useWorkspaceStore.getState();
      ws.select({ nodeIds: [nodeId] });
      ws.openInspector();
      track("node_selected", { projectId });
    },
    [projectId],
  );

  const clearSelection = useCallback(() => useWorkspaceStore.getState().clearSelection(), []);

  return { selectedNodeIds, selectedEdgeIds, setNodeSelection, setEdgeSelection, selectNode, clearSelection };
}
