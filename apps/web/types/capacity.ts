import type { z } from "zod";

import type {
  BottleneckSchema,
  CapacityAnalysisSchema,
  ComponentUtilizationSchema,
  EdgeThroughputSchema,
  EnvelopePointSchema,
} from "@/schemas/capacity";

export type CapacityAnalysis = z.infer<typeof CapacityAnalysisSchema>;
export type ComponentUtilization = z.infer<typeof ComponentUtilizationSchema>;
export type EdgeThroughput = z.infer<typeof EdgeThroughputSchema>;
export type Bottleneck = z.infer<typeof BottleneckSchema>;
export type EnvelopePoint = z.infer<typeof EnvelopePointSchema>;
