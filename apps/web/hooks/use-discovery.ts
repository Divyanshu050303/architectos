/**
 * Brownfield discovery (spec §44). Discovery runs are backend jobs;
 * saving one creates a new architecture version authored by "discovery".
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { getDiscoveryRun, listConnectors, saveDiscovery, startDiscovery } from "@/api/discovery";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { Architecture } from "@/types/architecture";
import type { DiscoveryRequest, DiscoveryRun } from "@/types/discovery";

import { invalidateArchitectureDependents } from "./use-architecture";

const DISCOVERY_POLL_INTERVAL_MS = 800;

export function useConnectors() {
  return useQuery({
    queryKey: queryKeys.discoveryConnectors(),
    queryFn: ({ signal }) => listConnectors(signal),
  });
}

export function useStartDiscovery(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: DiscoveryRequest) => startDiscovery(projectId, input),
    onSuccess: (run) => {
      queryClient.setQueryData<DiscoveryRun>(queryKeys.discoveryRun(run.id), run);
      track("discovery_started", { projectId, connector: run.connector });
    },
  });
}

/** Polls a discovery run while it is queued or running. */
export function useDiscoveryRun(runId: string | null) {
  return useQuery({
    queryKey: queryKeys.discoveryRun(runId ?? ""),
    queryFn: ({ signal }) => getDiscoveryRun(runId ?? "", signal),
    enabled: runId !== null,
    staleTime: 0,
    refetchInterval: (q) =>
      q.state.data?.status === "queued" || q.state.data?.status === "running"
        ? DISCOVERY_POLL_INTERVAL_MS
        : false,
  });
}

/** Saves the reviewed proposed architecture as a new version (409 "version_conflict" if stale). */
export function useSaveDiscovery(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: ({ runId, baseVersion }: { runId: string; baseVersion: number | null }) =>
      saveDiscovery(runId, baseVersion),
    onSuccess: (architecture, { runId }) => {
      queryClient.setQueryData<Architecture | null>(queryKeys.architecture(projectId), architecture);
      invalidateArchitectureDependents(queryClient, projectId);
      track("discovery_saved", { projectId, runId, version: architecture.version });
    },
  });
}
