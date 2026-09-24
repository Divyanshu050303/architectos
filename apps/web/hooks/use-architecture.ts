import { type QueryClient, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  compareVersions,
  generateArchitecture,
  getArchitecture,
  getArchitectureVersion,
  listArchitectureVersions,
  saveArchitectureCommands,
  type SaveCommandsInput,
  saveLayout,
  type SaveLayoutInput,
} from "@/api/architectures";
import { getJob } from "@/api/jobs";
import { applyCommand } from "@/features/architecture/utils/commands";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { Architecture } from "@/types/architecture";
import type { Job } from "@/types/project";

const JOB_POLL_INTERVAL_MS = 800;

/** The Job contract carries no projectId, so remember which project started each job. */
const jobProjects = new Map<string, string>();

/**
 * A new architecture version makes every analysis (capacity, validation, reliability,
 * security, observability, cost, drift, latest simulation) and the project summary stale.
 * Shared by every mutation that produces a version (commands, proposals, generation, discovery).
 */
export function invalidateArchitectureDependents(queryClient: QueryClient, projectId: string): void {
  for (const queryKey of [
    queryKeys.architectureVersions(projectId),
    queryKeys.capacity(projectId),
    queryKeys.validation(projectId),
    queryKeys.reliability(projectId),
    queryKeys.security(projectId),
    queryKeys.observability(projectId),
    queryKeys.cost(projectId),
    queryKeys.drift(projectId),
    queryKeys.simulationScope(projectId),
    queryKeys.evolution(projectId),
    queryKeys.evidenceList(projectId),
    queryKeys.project(projectId),
    queryKeys.projects(),
  ]) {
    void queryClient.invalidateQueries({ queryKey });
  }
}

/** `data` is null when the project has no architecture yet. */
export function useArchitecture(projectId: string) {
  return useQuery({
    queryKey: queryKeys.architecture(projectId),
    queryFn: ({ signal }) => getArchitecture(projectId, signal),
    enabled: Boolean(projectId),
  });
}

export function useArchitectureVersions(projectId: string) {
  return useQuery({
    queryKey: queryKeys.architectureVersions(projectId),
    queryFn: ({ signal }) => listArchitectureVersions(projectId, signal),
    enabled: Boolean(projectId),
  });
}

/** Historical versions are immutable, so they never go stale. */
export function useArchitectureVersion(projectId: string, version: number | null) {
  return useQuery({
    queryKey: queryKeys.architectureVersion(projectId, version ?? 0),
    queryFn: ({ signal }) => getArchitectureVersion(projectId, version ?? 0, signal),
    enabled: Boolean(projectId) && version !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });
}

/** Explicit save of semantic commands; the backend returns the new version (spec §91–92, §105). */
export function useSaveArchitectureCommands(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveCommandsInput) => saveArchitectureCommands(projectId, input),
    onSuccess: (architecture) => {
      queryClient.setQueryData<Architecture | null>(queryKeys.architecture(projectId), architecture);
      invalidateArchitectureDependents(queryClient, projectId);
    },
  });
}

/** Layout autosave. Never creates a version, so nothing else is invalidated (spec §67, §91). */
export function useSaveLayout(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (input: SaveLayoutInput) => saveLayout(projectId, input),
    onSuccess: (_result, { baseVersion, positions }) => {
      queryClient.setQueryData<Architecture | null>(queryKeys.architecture(projectId), (current) =>
        current && current.version === baseVersion
          ? applyCommand(current, { type: "MOVE_COMPONENTS", positions })
          : current,
      );
    },
  });
}

export function useGenerateArchitecture(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => generateArchitecture(projectId),
    onSuccess: (job) => {
      jobProjects.set(job.id, projectId);
      queryClient.setQueryData<Job>(queryKeys.job(job.id), job);
    },
  });
}

function isActive(job: Job | undefined): boolean {
  return job?.status === "queued" || job?.status === "running";
}

/** Polls a job while it is queued or running; refreshes project data once it succeeds. */
export function useJob(jobId: string | null) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.job(jobId ?? ""),
    queryFn: ({ signal }) => getJob(jobId ?? "", signal),
    enabled: jobId !== null,
    staleTime: 0,
    refetchInterval: (q) => (isActive(q.state.data) ? JOB_POLL_INTERVAL_MS : false),
  });

  const handledJobId = useRef<string | null>(null);
  const job = query.data;
  useEffect(() => {
    if (!job || job.status !== "succeeded" || handledJobId.current === job.id) return;
    handledJobId.current = job.id;
    const projectId = jobProjects.get(job.id);
    if (projectId) {
      void queryClient.invalidateQueries({ queryKey: queryKeys.architectureScope(projectId) });
      invalidateArchitectureDependents(queryClient, projectId);
    } else {
      // Job started elsewhere (e.g. before a reload): refresh every project's analysis data.
      const root = queryKeys.projectScope("")[0];
      void queryClient.invalidateQueries({ predicate: (q) => q.queryKey[0] === root });
      void queryClient.invalidateQueries({ queryKey: queryKeys.projects() });
    }
    track("architecture_generated", { jobId: job.id, version: job.result?.architectureVersion ?? 0 });
  }, [job, queryClient]);

  return query;
}

/**
 * Backend diff between two saved versions (spec §43, §93). Versions are immutable, so a
 * comparison never goes stale. Disabled until both sides are chosen.
 */
export function useCompareVersions(projectId: string, from: number | null, to: number | null) {
  return useQuery({
    queryKey: queryKeys.architectureComparison(projectId, from ?? 0, to ?? 0),
    queryFn: ({ signal }) => compareVersions(projectId, from ?? 0, to ?? 0, signal),
    enabled: Boolean(projectId) && from !== null && to !== null,
    staleTime: Number.POSITIVE_INFINITY,
  });
}
