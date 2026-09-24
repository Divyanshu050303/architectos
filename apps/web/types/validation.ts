import type { z } from "zod";

import type {
  FindingSchema,
  HealthCategorySchema,
  HealthCategoryScoreSchema,
  SeveritySchema,
  ValidationReportSchema,
} from "@/schemas/validation";

export type Severity = z.infer<typeof SeveritySchema>;
export type Finding = z.infer<typeof FindingSchema>;
export type HealthCategory = z.infer<typeof HealthCategorySchema>;
export type HealthCategoryScore = z.infer<typeof HealthCategoryScoreSchema>;
export type ValidationReport = z.infer<typeof ValidationReportSchema>;
