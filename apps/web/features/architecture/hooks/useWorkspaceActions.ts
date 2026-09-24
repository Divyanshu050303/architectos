/**
 * Workspace-level actions shared by the toolbar, keyboard shortcuts and the ⌘K
 * palette (spec §20, §50–51): add component, auto layout, compare versions, undo/redo,
 * save. Registers the shortcuts and palette commands while the workspace is mounted.
 */
import { useCallback, useMemo } from "react";

import { useWorkspaceStore } from "@/stores/workspace-store";
import type { ComponentDefinition } from "@/types/component";

import type { AutoLayoutKind } from "../utils/graph-layout";
import type { GraphView } from "../utils/graph-view";
import type { CanvasControls } from "./useArchitectureCanvas";
import { type ArchitectureCommandsApi, useWorkspaceShortcuts } from "./useArchitectureCommands";
import { useWorkspacePaletteCommands } from "./useWorkspacePaletteCommands";

export interface WorkspaceActionsInput {
  projectId: string;
  editable: boolean;
  isDirty: boolean;
  view: GraphView;
  setView: (view: GraphView) => void;
  latestVersion: number | null;
  viewingVersion: number | null;
  controls: CanvasControls;
  commands: ArchitectureCommandsApi;
  history: { canUndo: boolean; canRedo: boolean; undo: () => void; redo: () => void };
}

export function useWorkspaceActions({
  projectId,
  editable,
  isDirty,
  view,
  setView,
  latestVersion,
  viewingVersion,
  controls,
  commands,
  history,
}: WorkspaceActionsInput) {
  const addComponent = useCallback(
    (definition: ComponentDefinition) => void commands.addComponent(definition, controls.viewportCenter()),
    [commands, controls],
  );

  const autoLayout = useCallback(
    (kind: AutoLayoutKind) => {
      if (!commands.autoLayout(kind)) return;
      // Layouts place every component, so show them all.
      if (view !== "detailed") setView("detailed");
      requestAnimationFrame(() => controls.fitView());
    },
    [commands, controls, view, setView],
  );

  const openCompare = useCallback(
    (from: number | null, to: number | null) => useWorkspaceStore.getState().openCompare(from, to),
    [],
  );
  const compareVersions = useMemo(
    () =>
      latestVersion !== null && latestVersion > 1
        ? () => openCompare(viewingVersion ?? latestVersion - 1, latestVersion)
        : null,
    [latestVersion, viewingVersion, openCompare],
  );

  useWorkspaceShortcuts({
    undo: history.undo,
    redo: history.redo,
    save: commands.save,
    fitView: controls.fitView,
    deleteSelection: commands.deleteSelection,
    editable,
  });

  useWorkspacePaletteCommands({
    projectId,
    editable,
    canUndo: history.canUndo,
    canRedo: history.canRedo,
    isDirty,
    addComponent,
    autoLayout,
    fitView: controls.fitView,
    undo: history.undo,
    redo: history.redo,
    save: commands.save,
    compareVersions,
  });

  return { addComponent, autoLayout, openCompare };
}
