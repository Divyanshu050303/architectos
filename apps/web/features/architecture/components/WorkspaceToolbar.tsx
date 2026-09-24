"use client";

/**
 * Workspace wiring for ArchitectureToolbar (spec §20): reads view toggles, pending
 * edits and saved versions from the stores and routes actions to the workspace.
 * ArchitectureToolbar itself stays presentational.
 */
import { useShallow } from "zustand/react/shallow";

import { useArchitectureVersions } from "@/hooks/use-architecture";
import { useArchitectureStore } from "@/stores/architecture-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { AnalysisMode, ArchitectureVersionSummary } from "@/types/architecture";
import type { ComponentDefinition } from "@/types/component";

import type { CanvasControls } from "../hooks/useArchitectureCanvas";
import type { ArchitectureCommandsApi } from "../hooks/useArchitectureCommands";
import type { AutoLayoutKind } from "../utils/graph-layout";
import type { GraphView } from "../utils/graph-view";
import { ArchitectureToolbar } from "./ArchitectureToolbar";

const EMPTY_VERSIONS: readonly ArchitectureVersionSummary[] = [];

export interface WorkspaceToolbarProps {
  projectId: string;
  editable: boolean;
  mode: AnalysisMode;
  onModeChange: (mode: AnalysisMode) => void;
  view: GraphView;
  onViewChange: (view: GraphView) => void;
  domainsAvailable: boolean;
  controls: Pick<CanvasControls, "zoomIn" | "zoomOut" | "fitView">;
  history: { canUndo: boolean; canRedo: boolean; undo: () => void; redo: () => void };
  commands: Pick<ArchitectureCommandsApi, "isSaving" | "save" | "discard">;
  onAddComponent: (definition: ComponentDefinition) => void;
  onAutoLayout: (kind: AutoLayoutKind) => void;
  onCompare: (from: number | null, to: number | null) => void;
  onToggleFullscreen: () => void;
}

export function WorkspaceToolbar({
  projectId,
  editable,
  mode,
  onModeChange,
  view,
  onViewChange,
  domainsAvailable,
  controls,
  history,
  commands,
  onAddComponent,
  onAutoLayout,
  onCompare,
  onToggleFullscreen,
}: WorkspaceToolbarProps) {
  const ws = useWorkspaceStore(
    useShallow((s) => ({
      showGrid: s.showGrid,
      showMiniMap: s.showMiniMap,
      isFullscreen: s.isFullscreen,
      inspectorOpen: s.inspectorOpen,
      focusHops: s.focusHops,
    })),
  );
  const pendingCount = useArchitectureStore((s) => s.pending.length);
  const savedVersion = useArchitectureStore((s) => s.base?.version ?? null);
  const versions = useArchitectureVersions(projectId).data ?? EMPTY_VERSIONS;

  return (
    <ArchitectureToolbar
      editable={editable}
      mode={mode}
      onModeChange={onModeChange}
      onZoomIn={controls.zoomIn}
      onZoomOut={controls.zoomOut}
      onFitView={controls.fitView}
      showGrid={ws.showGrid}
      onToggleGrid={() => useWorkspaceStore.getState().setShowGrid(!ws.showGrid)}
      showMiniMap={ws.showMiniMap}
      onToggleMiniMap={() => useWorkspaceStore.getState().setShowMiniMap(!ws.showMiniMap)}
      isFullscreen={ws.isFullscreen}
      onToggleFullscreen={onToggleFullscreen}
      canUndo={history.canUndo}
      canRedo={history.canRedo}
      onUndo={history.undo}
      onRedo={history.redo}
      onAutoLayout={onAutoLayout}
      onAddComponent={onAddComponent}
      domainsAvailable={domainsAvailable}
      view={view}
      onViewChange={onViewChange}
      focusHops={ws.focusHops}
      onFocusHopsChange={(hops) => useWorkspaceStore.getState().setFocusHops(hops)}
      versions={versions}
      onCompare={onCompare}
      pendingCount={pendingCount}
      savedVersion={savedVersion}
      isSaving={commands.isSaving}
      onSave={commands.save}
      onDiscard={commands.discard}
      inspectorOpen={ws.inspectorOpen}
      onToggleInspector={() => {
        const store = useWorkspaceStore.getState();
        if (store.inspectorOpen) store.closeInspector();
        else store.openInspector();
      }}
    />
  );
}
