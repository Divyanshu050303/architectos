/** Security posture (spec §37, §68). `data` is null until the current version is analyzed; never optimistic (spec §50). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getSecurity, runSecurityAnalysis } from "@/api/security";
import { queryKeys } from "@/lib/query-keys";
import type { SecurityAnalysis } from "@/types/security";

export function useSecurity(projectId: string) {
  return useQuery({
    queryKey: queryKeys.security(projectId),
    queryFn: ({ signal }) => getSecurity(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useRunSecurity(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runSecurityAnalysis(projectId),
    onSuccess: (analysis) => {
      queryClient.setQueryData<SecurityAnalysis | null>(queryKeys.security(projectId), analysis);
      void queryClient.invalidateQueries({ queryKey: queryKeys.evidenceList(projectId) });
    },
  });
}
