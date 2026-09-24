/** Reliability analysis (spec §37, §70). `data` is null until the current version is analyzed; never optimistic (spec §50). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getReliability, runReliabilityAnalysis } from "@/api/reliability";
import { queryKeys } from "@/lib/query-keys";
import type { ReliabilityAnalysis } from "@/types/reliability";

export function useReliability(projectId: string) {
  return useQuery({
    queryKey: queryKeys.reliability(projectId),
    queryFn: ({ signal }) => getReliability(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useRunReliability(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runReliabilityAnalysis(projectId),
    onSuccess: (analysis) => {
      queryClient.setQueryData<ReliabilityAnalysis | null>(queryKeys.reliability(projectId), analysis);
      void queryClient.invalidateQueries({ queryKey: queryKeys.evidenceList(projectId) });
    },
  });
}
