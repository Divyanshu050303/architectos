import type { z } from "zod";

import type {
  CascadeRiskSchema,
  CriticalPathSchema,
  ReliabilityAnalysisSchema,
  SinglePointOfFailureSchema,
} from "@/schemas/reliability";

export type ReliabilityAnalysis = z.infer<typeof ReliabilityAnalysisSchema>;
export type SinglePointOfFailure = z.infer<typeof SinglePointOfFailureSchema>;
export type CriticalPath = z.infer<typeof CriticalPathSchema>;
export type CascadeRisk = z.infer<typeof CascadeRiskSchema>;
