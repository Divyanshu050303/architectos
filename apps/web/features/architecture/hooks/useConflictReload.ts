/**
 * Version conflict actions (spec §93): reload the latest saved version (discarding
 * the draft) or compare your version with it.
 */
import { useQueryClient } from "@tanstack/react-query";
import { useCallback, useState } from "react";

import { invalidateArchitectureDependents, useArchitecture } from "@/hooks/use-architecture";
import { useArchitectureStore } from "@/stores/architecture-store";
import { useUiStore } from "@/stores/ui-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

export function useConflictReload(projectId: string) {
  const queryClient = useQueryClient();
  const architectureQuery = useArchitecture(projectId);
  const [reloading, setReloading] = useState(false);

  const reloadLatest = useCallback(async () => {
    setReloading(true);
    try {
      const result = await architectureQuery.refetch();
      if (result.data) useArchitectureStore.getState().load(result.data, { force: true });
      else useArchitectureStore.getState().discard();
      invalidateArchitectureDependents(queryClient, projectId);
      useWorkspaceStore.getState().clearSelection();
      useUiStore.getState().dismissConflict();
    } finally {
      setReloading(false);
    }
  }, [architectureQuery, queryClient, projectId]);

  const compare = useCallback((yourVersion: number, latest: number) => {
    useUiStore.getState().dismissConflict();
    useWorkspaceStore.getState().openCompare(yourVersion, latest);
  }, []);

  return { reloading, reloadLatest, compare };
}
