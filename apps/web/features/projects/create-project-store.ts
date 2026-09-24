/** Open state of the single, shell-mounted "Create project" dialog (reachable from ⌘K and buttons). */
import { create } from "zustand";

interface CreateProjectDialogState {
  open: boolean;
  setOpen: (open: boolean) => void;
}

export const useCreateProjectDialog = create<CreateProjectDialogState>()((set) => ({
  open: false,
  setOpen: (open) => set({ open }),
}));

export function openCreateProjectDialog(): void {
  useCreateProjectDialog.getState().setOpen(true);
}
