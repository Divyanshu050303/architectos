/** Drift between the saved architecture and discovered infrastructure (spec §45). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { checkDrift, getDrift } from "@/api/drift";
import { queryKeys } from "@/lib/query-keys";
import type { DriftReport } from "@/types/discovery";

/** `data` is null until drift has been checked for the current architecture version. */
export function useDrift(projectId: string) {
  return useQuery({
    queryKey: queryKeys.drift(projectId),
    queryFn: ({ signal }) => getDrift(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useCheckDrift(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => checkDrift(projectId),
    onSuccess: (report) => {
      queryClient.setQueryData<DriftReport | null>(queryKeys.drift(projectId), report);
    },
  });
}
