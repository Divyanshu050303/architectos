import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getRequirements, type RequirementsInput, saveRequirements } from "@/api/requirements";
import { queryKeys } from "@/lib/query-keys";
import type { Requirements } from "@/types/project";

export function useRequirements(projectId: string) {
  return useQuery({
    queryKey: queryKeys.requirements(projectId),
    queryFn: ({ signal }) => getRequirements(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useSaveRequirements(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: RequirementsInput) => saveRequirements(projectId, input),
    onSuccess: (requirements) => {
      queryClient.setQueryData<Requirements>(queryKeys.requirements(projectId), requirements);
      // The project summary shows DAU / peak RPS from requirements.
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects() });
    },
  });
}
