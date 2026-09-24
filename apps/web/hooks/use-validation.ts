import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getValidation, runValidation, setFindingStatus } from "@/api/validation";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { Finding, ValidationReport } from "@/types/validation";

/** `data` is null until the current architecture version has been validated. */
export function useValidation(projectId: string) {
  return useQuery({
    queryKey: queryKeys.validation(projectId),
    queryFn: ({ signal }) => getValidation(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useRunValidation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => runValidation(projectId),
    onSuccess: (report) => {
      queryClient.setQueryData<ValidationReport | null>(queryKeys.validation(projectId), report);
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects() });
      track("architecture_validated", {
        projectId,
        version: report.architectureVersion,
        findings: report.findings.length,
      });
    },
  });
}

/**
 * Ignore / reopen a finding. Not optimistic (spec §50): the cache is updated with the
 * server's finding, and the report is refetched because health scores may change.
 */
export function useSetFindingStatus(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ findingId, status }: { findingId: string; status: Finding["status"] }) =>
      setFindingStatus(projectId, findingId, status),
    onSuccess: (finding) => {
      queryClient.setQueryData<ValidationReport | null>(queryKeys.validation(projectId), (report) =>
        report
          ? { ...report, findings: report.findings.map((f) => (f.id === finding.id ? finding : f)) }
          : report,
      );
      void queryClient.invalidateQueries({ queryKey: queryKeys.validation(projectId) });
      void queryClient.invalidateQueries({ queryKey: queryKeys.project(projectId) });
    },
  });
}
