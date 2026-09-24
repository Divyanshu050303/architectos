import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getCapacity, runCapacityAnalysis } from "@/api/capacity";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { CapacityAnalysis } from "@/types/capacity";

/** `data` is null until the current architecture version has been analyzed. */
export function useCapacity(projectId: string) {
  return useQuery({
    queryKey: queryKeys.capacity(projectId),
    queryFn: ({ signal }) => getCapacity(projectId, signal),
    enabled: Boolean(projectId),
  });
}

/** Not optimistic: capacity numbers come only from the backend engine (spec §50). */
export function useRunCapacityAnalysis(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runCapacityAnalysis(projectId),
    onSuccess: (analysis) => {
      queryClient.setQueryData<CapacityAnalysis | null>(queryKeys.capacity(projectId), analysis);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects() });
      track("capacity_analysis_completed", { projectId, version: analysis.architectureVersion });
    },
  });
}
