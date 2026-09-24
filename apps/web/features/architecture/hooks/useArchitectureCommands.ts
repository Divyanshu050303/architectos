/**
 * Every architecture edit goes through here as a semantic command (spec §106–107).
 * Semantic edits stay in the local draft until an explicit save; layout moves
 * autosave on a debounce and never create a version (spec §67, §91).
 */
import { useCallback, useEffect } from "react";

import { isApiError } from "@/api/client";
import { toast } from "@/components/ui/toast";
import { useSaveArchitectureCommands, useSaveLayout } from "@/hooks/use-architecture";
import { isEditableTarget, matchesShortcut } from "@/lib/keyboard";
import { createId, isRecord } from "@/lib/utils";
import { selectIsDirty, selectLayoutChanged, useArchitectureStore } from "@/stores/architecture-store";
import { type VersionConflict, useUiStore } from "@/stores/ui-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { ArchitectureEdge, Position } from "@/types/architecture";
import type { ComponentDefinition } from "@/types/component";

import { LAYOUT_AUTOSAVE_MS } from "../constants";
import type { ArchitectureCommand } from "../types";
import { autoLayout as computeLayout, type AutoLayoutKind, layoutDirectionOf } from "../utils/graph-layout";

const DUPLICATE_OFFSET = 40;

/** A 409 "version_conflict" becomes the conflict dialog's data (spec §93). */
export function conflictFrom(error: unknown, yourVersion: number): VersionConflict | null {
  if (!isApiError(error, "version_conflict")) return null;
  const latest = isRecord(error.details) ? error.details.latestVersion : undefined;
  return { yourVersion, latestVersion: typeof latest === "number" ? latest : yourVersion + 1 };
}

function uniqueId(prefix: string, taken: ReadonlySet<string>): string {
  let id = createId(prefix);
  while (taken.has(id)) id = createId(prefix);
  return id;
}

function nodeIds(): Set<string> {
  return new Set(useArchitectureStore.getState().present?.nodes.map((n) => n.id) ?? []);
}

export function useArchitectureCommands(projectId: string) {
  const saveMutation = useSaveArchitectureCommands(projectId);

  /** Apply a command to the draft; invalid commands surface as a toast, never a crash. */
  const run = useCallback((command: ArchitectureCommand): boolean => {
    const result = useArchitectureStore.getState().dispatch(command);
    if (!result.ok) toast("Change not applied", { description: result.error, tone: "danger" });
    return result.ok;
  }, []);

  const addComponent = useCallback(
    (definition: ComponentDefinition, position: Position) => {
      const id = uniqueId(definition.type, nodeIds());
      const ok = run({
        type: "ADD_COMPONENT",
        node: {
          id,
          type: definition.type,
          name: definition.name,
          technology: definition.technology,
          configuration: { ...definition.configuration },
          position,
        },
      });
      if (ok) {
        const ws = useWorkspaceStore.getState();
        ws.select({ nodeIds: [id] });
        ws.openInspector("overview");
      }
      return ok ? id : null;
    },
    [run],
  );

  const duplicate = useCallback(
    (nodeId: string) => {
      const source = useArchitectureStore.getState().present?.nodes.find((n) => n.id === nodeId);
      if (!source) return;
      const id = uniqueId(source.type, nodeIds());
      const ok = run({
        type: "ADD_COMPONENT",
        node: {
          ...source,
          id,
          name: `${source.name} copy`,
          configuration: { ...source.configuration },
          position: { x: source.position.x + DUPLICATE_OFFSET, y: source.position.y + DUPLICATE_OFFSET },
        },
      });
      if (ok) useWorkspaceStore.getState().select({ nodeIds: [id] });
    },
    [run],
  );

  const connect = useCallback(
    (source: string, target: string) => {
      const taken = new Set(useArchitectureStore.getState().present?.edges.map((e) => e.id) ?? []);
      const edge: ArchitectureEdge = {
        id: uniqueId("edge", taken),
        source,
        target,
        synchronous: true,
        critical: true,
      };
      return run({ type: "CONNECT_COMPONENTS", edge });
    },
    [run],
  );

  const moveNodes = useCallback(
    (positions: Record<string, Position>) => {
      if (Object.keys(positions).length > 0) run({ type: "MOVE_COMPONENTS", positions });
    },
    [run],
  );

  const autoLayout = useCallback(
    (kind: AutoLayoutKind) => {
      const present = useArchitectureStore.getState().present;
      if (!present || present.nodes.length === 0) return false;
      useWorkspaceStore.getState().setLayoutDirection(layoutDirectionOf(kind));
      return run({
        type: "MOVE_COMPONENTS",
        positions: computeLayout(kind, present.nodes, present.edges),
      });
    },
    [run],
  );

  const deleteSelection = useCallback(() => {
    const ws = useWorkspaceStore.getState();
    const { selectedNodeIds, selectedEdgeIds } = ws;
    if (selectedNodeIds.length === 0 && selectedEdgeIds.length === 0) return;
    if (selectedNodeIds.length > 0) run({ type: "REMOVE_COMPONENTS", nodeIds: selectedNodeIds });
    const remaining = new Set(useArchitectureStore.getState().present?.edges.map((e) => e.id) ?? []);
    const edgeIds = selectedEdgeIds.filter((id) => remaining.has(id));
    if (edgeIds.length > 0) run({ type: "REMOVE_CONNECTIONS", edgeIds });
    ws.clearSelection();
  }, [run]);

  const { mutate: saveCommands, isPending: isSaving } = saveMutation;

  const save = useCallback(() => {
    const { base, pending } = useArchitectureStore.getState();
    if (!base || pending.length === 0 || isSaving) return;
    saveCommands(
      { baseVersion: base.version, commands: pending },
      {
        onSuccess: (architecture) => {
          useArchitectureStore.getState().markSaved(architecture);
          toast(`Saved as v${architecture.version}`, { tone: "success" });
        },
        onError: (error) => {
          const conflict = conflictFrom(error, base.version);
          if (conflict) {
            useUiStore.getState().showConflict(conflict);
            return;
          }
          toast("Changes not saved", {
            description: error instanceof Error ? error.message : "The server rejected the change.",
            tone: "danger",
          });
        },
      },
    );
  }, [saveCommands, isSaving]);

  const discard = useCallback(() => {
    useArchitectureStore.getState().discard();
    useWorkspaceStore.getState().clearSelection();
    toast("Unsaved changes discarded");
  }, []);

  return {
    run,
    addComponent,
    duplicate,
    connect,
    moveNodes,
    autoLayout,
    deleteSelection,
    save,
    discard,
    isSaving,
  };
}

export type ArchitectureCommandsApi = ReturnType<typeof useArchitectureCommands>;

/**
 * Debounced layout autosave (spec §91). Only positions of components that exist in
 * the saved version are sent; unsaved components travel with the next explicit save.
 */
export function useLayoutAutosave(projectId: string) {
  const layoutChanged = useArchitectureStore(selectLayoutChanged);
  const present = useArchitectureStore((s) => s.present);
  const { mutate: saveLayout } = useSaveLayout(projectId);

  useEffect(() => {
    if (!layoutChanged) return;
    const timer = setTimeout(() => {
      const { base, present: draft } = useArchitectureStore.getState();
      if (!base || !draft) return;
      const saved = new Set(base.nodes.map((n) => n.id));
      const positions = Object.fromEntries(
        draft.nodes.filter((n) => saved.has(n.id)).map((n) => [n.id, n.position]),
      );
      saveLayout(
        { baseVersion: base.version, positions },
        {
          onSuccess: () => useArchitectureStore.getState().markLayoutSaved(),
          onError: (error) =>
            toast("Layout not saved", {
              description: error instanceof Error ? error.message : undefined,
              tone: "danger",
            }),
        },
      );
    }, LAYOUT_AUTOSAVE_MS);
    return () => clearTimeout(timer);
  }, [layoutChanged, present, saveLayout]);
}

export interface WorkspaceShortcutHandlers {
  undo: () => void;
  redo: () => void;
  save: () => void;
  fitView: () => void;
  deleteSelection: () => void;
  editable: boolean;
}

function insideOverlay(target: EventTarget | null): boolean {
  return (
    target instanceof Element && target.closest('[role="dialog"],[role="menu"],[role="listbox"]') !== null
  );
}

/** Workspace keyboard shortcuts (spec §60). Ignored while typing, except ⌘S. */
export function useWorkspaceShortcuts(handlers: WorkspaceShortcutHandlers) {
  const { undo, redo, save, fitView, deleteSelection, editable } = handlers;

  useEffect(() => {
    function onKeyDown(event: KeyboardEvent) {
      if (event.defaultPrevented) return;
      if (matchesShortcut(event, "mod+s")) {
        event.preventDefault();
        if (editable) save();
        return;
      }
      if (isEditableTarget(event.target) || insideOverlay(event.target)) return;

      if (matchesShortcut(event, "mod+shift+z")) {
        event.preventDefault();
        if (editable) redo();
      } else if (matchesShortcut(event, "mod+z")) {
        event.preventDefault();
        if (editable) undo();
      } else if (matchesShortcut(event, "f")) {
        event.preventDefault();
        fitView();
      } else if (matchesShortcut(event, "delete")) {
        if (!editable) return;
        event.preventDefault();
        deleteSelection();
      } else if (matchesShortcut(event, "esc")) {
        const ws = useWorkspaceStore.getState();
        if (ws.connectSourceId) ws.cancelConnect();
        else if (ws.inspectorOpen) ws.closeInspector();
      }
    }
    document.addEventListener("keydown", onKeyDown);
    return () => document.removeEventListener("keydown", onKeyDown);
  }, [undo, redo, save, fitView, deleteSelection, editable]);
}

/** Selector re-exported so callers do not need the store module for the dirty flag. */
export const useIsDirty = () => useArchitectureStore(selectIsDirty);
