import type { z } from "zod";

import type {
  ObservabilityAnalysisSchema,
  ObservabilityCoverageSchema,
  ObservabilityGapSchema,
  SloSchema,
  TelemetrySignalSchema,
} from "@/schemas/observability";

export type ObservabilityAnalysis = z.infer<typeof ObservabilityAnalysisSchema>;
export type ObservabilityCoverage = z.infer<typeof ObservabilityCoverageSchema>;
export type ObservabilityGap = z.infer<typeof ObservabilityGapSchema>;
export type Slo = z.infer<typeof SloSchema>;
export type TelemetrySignal = z.infer<typeof TelemetrySignalSchema>;
