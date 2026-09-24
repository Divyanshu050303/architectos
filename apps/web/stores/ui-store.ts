/** App-shell UI state: overlays and dialogs that are not deep-linked (spec §51). */
import { create } from "zustand";

export interface VersionConflict {
  yourVersion: number;
  latestVersion: number;
}

export interface UiState {
  mobileSidebarOpen: boolean;
  commandPaletteOpen: boolean;
  /** Evidence shown in the evidence drawer (spec §34). */
  evidenceId: string | null;
  /** Drives the version-conflict dialog (spec §93). */
  conflict: VersionConflict | null;
}

export interface UiActions {
  setMobileSidebarOpen: (open: boolean) => void;
  setCommandPaletteOpen: (open: boolean) => void;
  toggleCommandPalette: () => void;
  openEvidence: (evidenceId: string) => void;
  closeEvidence: () => void;
  showConflict: (conflict: VersionConflict) => void;
  dismissConflict: () => void;
  reset: () => void;
}

export type UiStore = UiState & UiActions;

const INITIAL_STATE: UiState = {
  mobileSidebarOpen: false,
  commandPaletteOpen: false,
  evidenceId: null,
  conflict: null,
};

export const useUiStore = create<UiStore>()((set) => ({
  ...INITIAL_STATE,
  setMobileSidebarOpen: (open) => set({ mobileSidebarOpen: open }),
  setCommandPaletteOpen: (open) => set({ commandPaletteOpen: open }),
  toggleCommandPalette: () => set((state) => ({ commandPaletteOpen: !state.commandPaletteOpen })),
  openEvidence: (evidenceId) => set({ evidenceId }),
  closeEvidence: () => set({ evidenceId: null }),
  showConflict: (conflict) => set({ conflict }),
  dismissConflict: () => set({ conflict: null }),
  reset: () => set(INITIAL_STATE),
}));
