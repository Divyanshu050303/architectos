/** Cost estimate (spec §71). `data` is null until the current version is calculated; never optimistic (spec §50). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { calculateCost, getCost } from "@/api/cost";
import { queryKeys } from "@/lib/query-keys";
import type { CostEstimate } from "@/types/cost";

export function useCost(projectId: string) {
  return useQuery({
    queryKey: queryKeys.cost(projectId),
    queryFn: ({ signal }) => getCost(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useCalculateCost(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => calculateCost(projectId),
    onSuccess: (estimate) => {
      queryClient.setQueryData<CostEstimate | null>(queryKeys.cost(projectId), estimate);
      void queryClient.invalidateQueries({ queryKey: queryKeys.evidenceList(projectId) });
    },
  });
}
