/** Undo/redo over the architecture draft (spec §90). History lives in the architecture store. */
import { selectCanRedo, selectCanUndo, useArchitectureStore } from "@/stores/architecture-store";

export function useArchitectureHistory() {
  const canUndo = useArchitectureStore(selectCanUndo);
  const canRedo = useArchitectureStore(selectCanRedo);
  const undo = useArchitectureStore((s) => s.undo);
  const redo = useArchitectureStore((s) => s.redo);
  return { canUndo, canRedo, undo, redo };
}
