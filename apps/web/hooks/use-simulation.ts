/**
 * Simulations (spec §40–41, §72). Runs are backend jobs: the hooks start them, poll them
 * while they are queued or running, and display whatever the engine returns.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";

import {
  getLatestSimulation,
  getSimulationRun,
  listSimulationScenarios,
  startSimulation,
} from "@/api/simulations";
import { track } from "@/lib/analytics";
import { queryKeys } from "@/lib/query-keys";
import type { SimulationConfig, SimulationRun } from "@/types/simulation";

const SIMULATION_POLL_INTERVAL_MS = 700;

function isActive(run: SimulationRun | null | undefined): boolean {
  return run?.status === "queued" || run?.status === "running";
}

export function useSimulationScenarios(projectId: string) {
  return useQuery({
    queryKey: queryKeys.simulationScenarios(projectId),
    queryFn: ({ signal }) => listSimulationScenarios(projectId, signal),
    enabled: Boolean(projectId),
  });
}

/** `data` is null when the current architecture has never been simulated. */
export function useLatestSimulation(projectId: string) {
  return useQuery({
    queryKey: queryKeys.latestSimulation(projectId),
    queryFn: ({ signal }) => getLatestSimulation(projectId, signal),
    enabled: Boolean(projectId),
    refetchInterval: (q) => (isActive(q.state.data) ? SIMULATION_POLL_INTERVAL_MS : false),
  });
}

export function useRunSimulation(projectId: string) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (config: SimulationConfig) => startSimulation(projectId, config),
    onSuccess: (run) => {
      queryClient.setQueryData<SimulationRun>(queryKeys.simulationRun(run.id), run);
      queryClient.setQueryData<SimulationRun | null>(queryKeys.latestSimulation(projectId), run);
      track("simulation_started", {
        projectId,
        scenarioId: run.scenarioId,
        version: run.architectureVersion,
      });
    },
  });
}

/** Polls a run while it is queued or running; refreshes the project's latest run once it finishes. */
export function useSimulationRun(runId: string | null) {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: queryKeys.simulationRun(runId ?? ""),
    queryFn: ({ signal }) => getSimulationRun(runId ?? "", signal),
    enabled: runId !== null,
    staleTime: 0,
    refetchInterval: (q) => (isActive(q.state.data) ? SIMULATION_POLL_INTERVAL_MS : false),
  });

  const handledRunId = useRef<string | null>(null);
  const run = query.data;
  useEffect(() => {
    if (!run || isActive(run) || handledRunId.current === run.id) return;
    handledRunId.current = run.id;
    queryClient.setQueryData<SimulationRun | null>(queryKeys.latestSimulation(run.projectId), (latest) =>
      !latest || latest.id === run.id ? run : latest,
    );
    void queryClient.invalidateQueries({ queryKey: queryKeys.latestSimulation(run.projectId) });
    if (run.status === "succeeded") {
      track("simulation_completed", {
        projectId: run.projectId,
        runId: run.id,
        impact: run.result?.impact ?? "unknown",
      });
    }
  }, [run, queryClient]);

  return query;
}
