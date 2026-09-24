import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { createDecision, listDecisions } from "@/api/decisions";
import { queryKeys } from "@/lib/query-keys";
import type { Decision, DecisionInput } from "@/types/architecture";

export function useDecisions(projectId: string) {
  return useQuery({
    queryKey: queryKeys.decisions(projectId),
    queryFn: ({ signal }) => listDecisions(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useCreateDecision(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: DecisionInput) => createDecision(projectId, input),
    onSuccess: (decision) => {
      queryClient.setQueryData<Decision[]>(queryKeys.decisions(projectId), (list) =>
        list ? [...list, decision] : list,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.decisions(projectId) });
    },
  });
}
