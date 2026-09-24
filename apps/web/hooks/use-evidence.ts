import { useQuery } from "@tanstack/react-query";

import { getEvidence, listEvidence } from "@/api/evidence";
import { queryKeys } from "@/lib/query-keys";

/** Evidence records are immutable once produced. */
export function useEvidence(evidenceId: string | null) {
  return useQuery({
    queryKey: queryKeys.evidence(evidenceId ?? ""),
    queryFn: ({ signal }) => getEvidence(evidenceId ?? "", signal),
    enabled: evidenceId !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Every evidence record cited by the project's analyses (evidence explorer). */
export function useEvidenceList(projectId: string) {
  return useQuery({
    queryKey: queryKeys.evidenceList(projectId),
    queryFn: ({ signal }) => listEvidence(projectId, signal),
    enabled: Boolean(projectId),
  });
}
