/**
 * Failure and load simulations (spec §40–41, §72). Every result — affected components,
 * metrics, timeline — is produced by the backend simulation engine; the frontend only
 * renders it.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/simulations.py (no engine yet).
 */
import { z } from "zod";

import { JobErrorSchema, JobStatusSchema, JobStepSchema } from "./api";

export const SIMULATION_SCENARIO_KINDS = [
  "traffic_spike",
  "database_failure",
  "redis_failure",
  "kafka_failure",
  "region_failure",
  "network_partition",
] as const;
export const SimulationScenarioKindSchema = z.enum(SIMULATION_SCENARIO_KINDS);

export const SimulationScenarioSchema = z.object({
  id: z.string(),
  kind: SimulationScenarioKindSchema,
  label: z.string(),
  description: z.string(),
  targetNodeIds: z.array(z.string()),
});

export const SimulationScenarioListSchema = z.array(SimulationScenarioSchema);

export const SIMULATION_TRAFFIC = ["current", "peak", "2x", "10x"] as const;
export const SIMULATION_ENVIRONMENTS = ["production_like", "staging"] as const;

/** Request body of POST /projects/{id}/simulations; echoed back as `SimulationRun.config`. */
export const SimulationConfigSchema = z.object({
  scenarioId: z.string().min(1),
  traffic: z.enum(SIMULATION_TRAFFIC),
  durationMinutes: z.number().int().positive(),
  environment: z.enum(SIMULATION_ENVIRONMENTS),
});

export const SimulationImpactSchema = z.enum(["low", "medium", "high", "critical"]);

/** Order of the propagation story shown on the canvas (spec §41). */
export const SIMULATION_PHASES = [
  "failure",
  "dependency",
  "load_increase",
  "resource_pressure",
  "latency",
  "potential_failure",
] as const;
export const SimulationPhaseSchema = z.enum(SIMULATION_PHASES);

export const SimulationTimelineEventSchema = z.object({
  atSeconds: z.number().min(0),
  phase: SimulationPhaseSchema,
  nodeIds: z.array(z.string()),
  description: z.string(),
});

export const SimulationMetricSchema = z.object({
  metric: z.string(),
  before: z.number(),
  after: z.number(),
  unit: z.string(),
});

export const SimulationResultSchema = z.object({
  impact: SimulationImpactSchema,
  affectedNodeIds: z.array(z.string()),
  metrics: z.array(SimulationMetricSchema),
  /** Fractions, 0..1 */
  errorRate: z.object({ before: z.number().min(0), after: z.number().min(0) }),
  cascadingFailure: z.enum(["none", "potential", "likely"]),
  timeline: z.array(SimulationTimelineEventSchema),
  evidenceIds: z.array(z.string()),
});

export const SimulationRunSchema = z.object({
  id: z.string(),
  projectId: z.string(),
  architectureVersion: z.number().int(),
  scenarioId: z.string(),
  config: SimulationConfigSchema,
  status: JobStatusSchema,
  steps: z.array(JobStepSchema),
  result: SimulationResultSchema.nullable(),
  error: JobErrorSchema.nullable(),
});
