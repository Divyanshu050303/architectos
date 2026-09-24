import type { z } from "zod";

import type {
  SimulationConfigSchema,
  SimulationImpactSchema,
  SimulationMetricSchema,
  SimulationPhaseSchema,
  SimulationResultSchema,
  SimulationRunSchema,
  SimulationScenarioKindSchema,
  SimulationScenarioSchema,
  SimulationTimelineEventSchema,
} from "@/schemas/simulation";

export type SimulationScenarioKind = z.infer<typeof SimulationScenarioKindSchema>;
export type SimulationScenario = z.infer<typeof SimulationScenarioSchema>;
export type SimulationConfig = z.infer<typeof SimulationConfigSchema>;
export type SimulationImpact = z.infer<typeof SimulationImpactSchema>;
export type SimulationPhase = z.infer<typeof SimulationPhaseSchema>;
export type SimulationTimelineEvent = z.infer<typeof SimulationTimelineEventSchema>;
export type SimulationMetric = z.infer<typeof SimulationMetricSchema>;
export type SimulationResult = z.infer<typeof SimulationResultSchema>;
export type SimulationRun = z.infer<typeof SimulationRunSchema>;
