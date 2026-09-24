/** Evolution roadmap and stage comparison (spec §42–43). */
import { useQuery } from "@tanstack/react-query";

import { compareStages, getEvolution } from "@/api/evolution";
import { queryKeys } from "@/lib/query-keys";

/** `data` is null when the project has no roadmap yet. */
export function useEvolution(projectId: string) {
  return useQuery({
    queryKey: queryKeys.evolution(projectId),
    queryFn: ({ signal }) => getEvolution(projectId, signal),
    enabled: Boolean(projectId),
  });
}

/** Backend diff between two evolution stages. Disabled until both stages are chosen. */
export function useCompareStages(projectId: string, fromStageId: string | null, toStageId: string | null) {
  return useQuery({
    queryKey: queryKeys.evolutionComparison(projectId, fromStageId ?? "", toStageId ?? ""),
    queryFn: ({ signal }) => compareStages(projectId, fromStageId ?? "", toStageId ?? "", signal),
    enabled: Boolean(projectId) && fromStageId !== null && toStageId !== null,
  });
}
