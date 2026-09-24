/**
 * Simulation overlay playback (spec §41, §72). Replays the backend simulation timeline
 * step by step; nothing is simulated here. Each step is the cumulative state after the
 * backend's timeline events up to that point, precomputed once so playback allocates
 * nothing per frame.
 */
import { SIMULATION_PHASES } from "@/schemas/simulation";
import type { SimulationPhase, SimulationTimelineEvent } from "@/types/simulation";

/** normal → failed (danger) → impacted (warning) → cascade (potential failure). */
export type SimulationNodeState = "normal" | "failed" | "impacted" | "cascade";

export const PHASE_LABELS: Record<SimulationPhase, string> = {
  failure: "Failure",
  dependency: "Dependency",
  load_increase: "Load increase",
  resource_pressure: "Resource pressure",
  latency: "Latency",
  potential_failure: "Potential failure",
};

export const PHASE_ORDER: readonly SimulationPhase[] = SIMULATION_PHASES;

const PHASE_STATE: Record<SimulationPhase, SimulationNodeState> = {
  failure: "failed",
  dependency: "impacted",
  load_increase: "impacted",
  resource_pressure: "impacted",
  latency: "impacted",
  potential_failure: "cascade",
};

/** A node keeps its most severe state once reached. */
const STATE_RANK: Record<SimulationNodeState, number> = { normal: 0, impacted: 1, cascade: 2, failed: 3 };

export interface PlaybackStep {
  /** 0 is the architecture before the scenario starts. */
  index: number;
  event: SimulationTimelineEvent | null;
  states: ReadonlyMap<string, SimulationNodeState>;
  /** Canonical phases reached so far, for the phase breadcrumb. */
  reached: ReadonlySet<SimulationPhase>;
}

export function buildPlaybackSteps(timeline: readonly SimulationTimelineEvent[]): PlaybackStep[] {
  const events = [...timeline].sort((a, b) => a.atSeconds - b.atSeconds);
  const steps: PlaybackStep[] = [{ index: 0, event: null, states: new Map(), reached: new Set() }];
  let states = new Map<string, SimulationNodeState>();
  let reached = new Set<SimulationPhase>();
  events.forEach((event, i) => {
    states = new Map(states);
    reached = new Set(reached).add(event.phase);
    const next = PHASE_STATE[event.phase];
    for (const nodeId of event.nodeIds) {
      const current = states.get(nodeId) ?? "normal";
      if (STATE_RANK[next] > STATE_RANK[current]) states.set(nodeId, next);
    }
    steps.push({ index: i + 1, event, states, reached });
  });
  return steps;
}

export function stepLabel(step: PlaybackStep | undefined): string {
  return step?.event ? PHASE_LABELS[step.event.phase] : "Normal";
}
