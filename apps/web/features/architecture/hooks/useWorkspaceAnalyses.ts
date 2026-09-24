/**
 * Server analyses behind the workspace overlays (spec §68–72): each backend analysis,
 * shown only for the version it was computed for in the read-only history view, plus
 * the simulation run replayed by the simulation overlay. Nothing is calculated here.
 */
import { useCallback, useMemo, useState } from "react";

import { useCapacity } from "@/hooks/use-capacity";
import { useCost } from "@/hooks/use-cost";
import { useObservability } from "@/hooks/use-observability";
import { useReliability } from "@/hooks/use-reliability";
import { useSecurity } from "@/hooks/use-security";
import { useLatestSimulation, useSimulationRun, useSimulationScenarios } from "@/hooks/use-simulation";
import { useValidation } from "@/hooks/use-validation";
import type { AnalysisMode } from "@/types/architecture";
import type { Finding } from "@/types/validation";

import { fetchingForVersion } from "../utils/overlay-chrome";
import { buildPlaybackSteps } from "../utils/simulation-playback";

const EMPTY_FINDINGS: readonly Finding[] = [];

/** An analysis is shown only for the version it was computed for (read-only history view). */
export function forVersion<T extends { architectureVersion: number }>(
  analysis: T | null,
  version: number | null,
): T | null {
  return analysis && (version === null || analysis.architectureVersion === version) ? analysis : null;
}

export interface WorkspaceAnalysesInput {
  projectId: string;
  /** Historical version being viewed; null for the draft (which keeps its latest analyses). */
  analysisVersion: number | null;
  /** The version on screen, for "still loading for this version". */
  shownVersion: number | null;
}

export function useWorkspaceAnalyses({ projectId, analysisVersion, shownVersion }: WorkspaceAnalysesInput) {
  const capacityQuery = useCapacity(projectId);
  const validationQuery = useValidation(projectId);
  const reliabilityQuery = useReliability(projectId);
  const securityQuery = useSecurity(projectId);
  const observabilityQuery = useObservability(projectId);
  const costQuery = useCost(projectId);

  const validation = forVersion(validationQuery.data ?? null, analysisVersion);
  return {
    capacity: forVersion(capacityQuery.data ?? null, analysisVersion),
    validation,
    findings: validation?.findings ?? EMPTY_FINDINGS,
    reliability: forVersion(reliabilityQuery.data ?? null, analysisVersion),
    security: forVersion(securityQuery.data ?? null, analysisVersion),
    observability: forVersion(observabilityQuery.data ?? null, analysisVersion),
    cost: forVersion(costQuery.data ?? null, analysisVersion),
    pending: {
      capacity: capacityQuery.isPending,
      validation: validationQuery.isPending,
      reliability: reliabilityQuery.isPending,
      security: securityQuery.isPending,
      cost: costQuery.isPending,
      observability: observabilityQuery.isPending,
    },
    fetching: {
      capacity: fetchingForVersion(capacityQuery, shownVersion),
      validation: fetchingForVersion(validationQuery, shownVersion),
      reliability: fetchingForVersion(reliabilityQuery, shownVersion),
      security: fetchingForVersion(securityQuery, shownVersion),
      cost: fetchingForVersion(costQuery, shownVersion),
      observability: fetchingForVersion(observabilityQuery, shownVersion),
    },
  };
}

export interface SimulationReplayInput {
  projectId: string;
  mode: AnalysisMode;
  /** `?simulation=<runId>`; null replays the latest run. */
  runId: string | null;
  analysisVersion: number | null;
  shownVersion: number | null;
}

const EMPTY_IDS: ReadonlySet<string> = new Set();

/** Simulation overlay replay (spec §41, §72): the run, its playback steps and position. */
export function useSimulationReplay({
  projectId,
  mode,
  runId,
  analysisVersion,
  shownVersion,
}: SimulationReplayInput) {
  const latest = useLatestSimulation(projectId);
  const explicit = useSimulationRun(runId);
  const query = runId ? explicit : latest;
  const run = forVersion(query.data ?? null, analysisVersion);
  const result = run?.status === "succeeded" ? run.result : null;
  const scenarios = useSimulationScenarios(projectId).data;

  const steps = useMemo(() => (result ? buildPlaybackSteps(result.timeline) : []), [result]);
  /** Every component the run touches, for the `simulating` state while playing. */
  const runNodeIds = useMemo(() => {
    const last = steps[steps.length - 1];
    return last ? new Set(last.states.keys()) : EMPTY_IDS;
  }, [steps]);

  const [playback, setPlayback] = useState<{ runId: string; index: number } | null>(null);
  // Opens on the run's final state; playback and the scrubber step through it.
  const stepIndex = run && playback?.runId === run.id ? playback.index : Math.max(steps.length - 1, 0);
  const setStepIndex = useCallback(
    (index: number) => {
      if (run) setPlayback({ runId: run.id, index });
    },
    [run],
  );
  const [playing, setPlaying] = useState(false);

  const active = mode === "simulation" && steps.length > 0;
  const scenarioLabel = run ? (scenarios?.find((s) => s.id === run.config.scenarioId)?.label ?? null) : null;

  return {
    run,
    result,
    scenarios,
    scenarioLabel,
    steps,
    stepIndex,
    setStepIndex,
    setPlaying,
    /** Node states at the current step, for the simulation overlay. */
    states: active ? (steps[stepIndex]?.states ?? null) : null,
    /** Components in the run while playback is running. */
    activeNodeIds: active && playing ? runNodeIds : null,
    pending: query.isPending,
    fetching: fetchingForVersion(query, shownVersion),
  };
}

export type WorkspaceAnalyses = ReturnType<typeof useWorkspaceAnalyses>;
export type SimulationReplay = ReturnType<typeof useSimulationReplay>;
