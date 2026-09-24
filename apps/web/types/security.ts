import type { z } from "zod";

import type {
  ExposureLevelSchema,
  ExposureSchema,
  SecurityAnalysisSchema,
  SecurityControlsSchema,
  StrideCategorySchema,
  ThreatSchema,
  TrustBoundarySchema,
} from "@/schemas/security";

export type SecurityAnalysis = z.infer<typeof SecurityAnalysisSchema>;
export type TrustBoundary = z.infer<typeof TrustBoundarySchema>;
export type ExposureLevel = z.infer<typeof ExposureLevelSchema>;
export type Exposure = z.infer<typeof ExposureSchema>;
export type SecurityControls = z.infer<typeof SecurityControlsSchema>;
export type StrideCategory = z.infer<typeof StrideCategorySchema>;
export type Threat = z.infer<typeof ThreatSchema>;
