/** Observability coverage and SLOs (spec §37, §68). `data` is null until the current version is analyzed. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getObservability, runObservabilityAnalysis } from "@/api/observability";
import { queryKeys } from "@/lib/query-keys";
import type { ObservabilityAnalysis } from "@/types/observability";

export function useObservability(projectId: string) {
  return useQuery({
    queryKey: queryKeys.observability(projectId),
    queryFn: ({ signal }) => getObservability(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useRunObservability(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runObservabilityAnalysis(projectId),
    onSuccess: (analysis) => {
      queryClient.setQueryData<ObservabilityAnalysis | null>(queryKeys.observability(projectId), analysis);
    },
  });
}
