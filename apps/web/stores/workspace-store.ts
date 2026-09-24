/**
 * Canvas workspace UI state (spec §51). Analysis mode, graph view, expanded domain,
 * simulation run and the deep-linked `?node=` live in the URL, not here (spec §52).
 */
import { create } from "zustand";

import type { LayoutDirection } from "@/features/architecture/utils/graph-layout";

export type { LayoutDirection };

export interface WorkspaceState {
  selectedNodeIds: string[];
  selectedEdgeIds: string[];
  inspectorOpen: boolean;
  inspectorTab: string;
  highlightedNodeIds: string[];
  /** Increments on every highlight so a repeated "Locate" re-triggers focus. */
  highlightToken: number;
  /** Source node while in "Add connection" mode. */
  connectSourceId: string | null;
  showGrid: boolean;
  showMiniMap: boolean;
  isFullscreen: boolean;
  layoutDirection: LayoutDirection;
  /** Focused view: neighbourhood depth around the selected component (spec §65). */
  focusHops: 1 | 2;
  /** Version comparison dialog (spec §43, §93); null when closed. */
  compare: { from: number | null; to: number | null } | null;
}

export interface WorkspaceActions {
  select: (ids: { nodeIds?: string[]; edgeIds?: string[] }) => void;
  toggleSelection: (nodeId: string) => void;
  clearSelection: () => void;
  openInspector: (tab?: string) => void;
  closeInspector: () => void;
  setInspectorTab: (tab: string) => void;
  highlight: (nodeIds: string[]) => void;
  clearHighlight: () => void;
  startConnect: (nodeId: string) => void;
  cancelConnect: () => void;
  setShowGrid: (value: boolean) => void;
  setShowMiniMap: (value: boolean) => void;
  setFullscreen: (value: boolean) => void;
  setLayoutDirection: (direction: LayoutDirection) => void;
  setFocusHops: (hops: 1 | 2) => void;
  openCompare: (from: number | null, to: number | null) => void;
  closeCompare: () => void;
  reset: () => void;
}

export type WorkspaceStore = WorkspaceState & WorkspaceActions;

export const DEFAULT_INSPECTOR_TAB = "overview";

const INITIAL_STATE: WorkspaceState = {
  selectedNodeIds: [],
  selectedEdgeIds: [],
  inspectorOpen: false,
  inspectorTab: DEFAULT_INSPECTOR_TAB,
  highlightedNodeIds: [],
  highlightToken: 0,
  connectSourceId: null,
  showGrid: true,
  showMiniMap: true,
  isFullscreen: false,
  layoutDirection: "TB",
  focusHops: 1,
  compare: null,
};

export const useWorkspaceStore = create<WorkspaceStore>()((set) => ({
  ...INITIAL_STATE,

  select: ({ nodeIds = [], edgeIds = [] }) => set({ selectedNodeIds: nodeIds, selectedEdgeIds: edgeIds }),

  toggleSelection: (nodeId) =>
    set((state) => ({
      selectedNodeIds: state.selectedNodeIds.includes(nodeId)
        ? state.selectedNodeIds.filter((id) => id !== nodeId)
        : [...state.selectedNodeIds, nodeId],
    })),

  clearSelection: () => set({ selectedNodeIds: [], selectedEdgeIds: [] }),

  openInspector: (tab) => set((state) => ({ inspectorOpen: true, inspectorTab: tab ?? state.inspectorTab })),

  closeInspector: () => set({ inspectorOpen: false }),

  setInspectorTab: (tab) => set({ inspectorTab: tab }),

  highlight: (nodeIds) =>
    set((state) => ({ highlightedNodeIds: nodeIds, highlightToken: state.highlightToken + 1 })),

  clearHighlight: () => set({ highlightedNodeIds: [] }),

  startConnect: (nodeId) => set({ connectSourceId: nodeId }),

  cancelConnect: () => set({ connectSourceId: null }),

  setShowGrid: (value) => set({ showGrid: value }),
  setShowMiniMap: (value) => set({ showMiniMap: value }),
  setFullscreen: (value) => set({ isFullscreen: value }),
  setLayoutDirection: (direction) => set({ layoutDirection: direction }),
  setFocusHops: (hops) => set({ focusHops: hops }),
  openCompare: (from, to) => set({ compare: { from, to } }),
  closeCompare: () => set({ compare: null }),

  reset: () => set(INITIAL_STATE),
}));
