/**
 * Local DRAFT of the architecture being edited (spec §90–92).
 *
 * The server copy lives in TanStack Query; this store only holds unsaved client
 * edits on top of the last server architecture (`base`). Every edit is a semantic
 * ArchitectureCommand applied through the pure `applyCommand` reducer, so undo/redo
 * is a matter of swapping snapshots. Layout moves are undoable but never pending:
 * they autosave separately and never create an architecture version (spec §67, §91).
 */
import { create } from "zustand";

import { CommandError, type ArchitectureCommand } from "@/features/architecture/types";
import { applyCommand, isSemanticCommand } from "@/features/architecture/utils/commands";
import type { Architecture } from "@/types/architecture";

export const MAX_HISTORY = 100;

export interface ArchitectureSnapshot {
  architecture: Architecture;
  pending: ArchitectureCommand[];
}

export type DispatchResult = { ok: true } | { ok: false; error: string };

export interface ArchitectureDraftState {
  projectId: string | null;
  /** Last architecture received from the server. */
  base: Architecture | null;
  /** Current draft shown on the canvas. */
  present: Architecture | null;
  past: ArchitectureSnapshot[];
  future: ArchitectureSnapshot[];
  /** Semantic commands applied since `base` (layout moves excluded). */
  pending: ArchitectureCommand[];
  lastError: string | null;
}

export interface ArchitectureDraftActions {
  /**
   * Adopt a server architecture. Replaces the draft only when the project changes,
   * `force` is set, or the draft has no unsaved edits and the server version differs.
   */
  load: (architecture: Architecture, options?: { force?: boolean }) => void;
  dispatch: (command: ArchitectureCommand) => DispatchResult;
  undo: () => void;
  redo: () => void;
  /** The server accepted the draft: it becomes the new base. */
  markSaved: (serverArchitecture: Architecture) => void;
  /** Layout autosave succeeded: base positions now match the draft. */
  markLayoutSaved: () => void;
  /** Drop all unsaved edits. */
  discard: () => void;
  reset: () => void;
}

export type ArchitectureStore = ArchitectureDraftState & ArchitectureDraftActions;

const INITIAL_STATE: ArchitectureDraftState = {
  projectId: null,
  base: null,
  present: null,
  past: [],
  future: [],
  pending: [],
  lastError: null,
};

function freshDraft(architecture: Architecture): ArchitectureDraftState {
  return {
    projectId: architecture.projectId,
    base: architecture,
    present: architecture,
    past: [],
    future: [],
    pending: [],
    lastError: null,
  };
}

function pushBounded(stack: readonly ArchitectureSnapshot[], snapshot: ArchitectureSnapshot) {
  const next = [...stack, snapshot];
  return next.length > MAX_HISTORY ? next.slice(next.length - MAX_HISTORY) : next;
}

export const useArchitectureStore = create<ArchitectureStore>()((set, get) => ({
  ...INITIAL_STATE,

  load: (architecture, options) => {
    const state = get();
    const shouldReplace =
      options?.force === true ||
      state.present === null ||
      state.projectId !== architecture.projectId ||
      (!selectIsDirty(state) && state.base?.version !== architecture.version);
    if (shouldReplace) set(freshDraft(architecture));
  },

  dispatch: (command) => {
    const { present, pending, past } = get();
    if (!present) {
      const error = "No architecture is loaded";
      set({ lastError: error });
      return { ok: false, error };
    }
    let next: Architecture;
    try {
      next = applyCommand(present, command);
    } catch (error) {
      if (error instanceof CommandError) {
        set({ lastError: error.message });
        return { ok: false, error: error.message };
      }
      throw error;
    }
    set({
      present: next,
      pending: isSemanticCommand(command) ? [...pending, command] : pending,
      past: pushBounded(past, { architecture: present, pending }),
      future: [],
      lastError: null,
    });
    return { ok: true };
  },

  undo: () => {
    const { past, future, present, pending } = get();
    const previous = past.at(-1);
    if (!previous || !present) return;
    set({
      present: previous.architecture,
      pending: previous.pending,
      past: past.slice(0, -1),
      future: pushBounded(future, { architecture: present, pending }),
      lastError: null,
    });
  },

  redo: () => {
    const { past, future, present, pending } = get();
    const next = future.at(-1);
    if (!next || !present) return;
    set({
      present: next.architecture,
      pending: next.pending,
      past: pushBounded(past, { architecture: present, pending }),
      future: future.slice(0, -1),
      lastError: null,
    });
  },

  markSaved: (serverArchitecture) => set(freshDraft(serverArchitecture)),

  markLayoutSaved: () => {
    const { base, present } = get();
    if (!base || !present) return;
    const positions = new Map(present.nodes.map((n) => [n.id, n.position]));
    set({
      base: {
        ...base,
        nodes: base.nodes.map((n) => {
          const position = positions.get(n.id);
          return position ? { ...n, position } : n;
        }),
      },
    });
  },

  discard: () => {
    const { base } = get();
    set({ present: base, pending: [], past: [], future: [], lastError: null });
  },

  reset: () => set(INITIAL_STATE),
}));

export function selectIsDirty(state: Pick<ArchitectureDraftState, "pending">): boolean {
  return state.pending.length > 0;
}

export function selectCanUndo(state: Pick<ArchitectureDraftState, "past">): boolean {
  return state.past.length > 0;
}

export function selectCanRedo(state: Pick<ArchitectureDraftState, "future">): boolean {
  return state.future.length > 0;
}

/** True when any node in the draft sits somewhere other than in `base`. */
export function selectLayoutChanged(state: Pick<ArchitectureDraftState, "base" | "present">): boolean {
  const { base, present } = state;
  if (!base || !present) return false;
  const basePositions = new Map(base.nodes.map((n) => [n.id, n.position]));
  return present.nodes.some((n) => {
    const before = basePositions.get(n.id);
    return before !== undefined && (before.x !== n.position.x || before.y !== n.position.y);
  });
}

/** Node positions of the draft, e.g. for the layout autosave payload. */
export function selectPositions(
  state: Pick<ArchitectureDraftState, "present">,
): Record<string, { x: number; y: number }> {
  return Object.fromEntries((state.present?.nodes ?? []).map((n) => [n.id, n.position]));
}
