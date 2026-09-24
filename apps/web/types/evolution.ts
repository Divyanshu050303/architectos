import type { z } from "zod";

import type {
  ArchitectureComparisonSchema,
  ComparisonChangeSchema,
  ComparisonValueSchema,
  ComponentChangeSchema,
  ConnectionChangeSchema,
  FieldChangeSchema,
} from "@/schemas/comparison";
import type {
  EvolutionChangeSchema,
  EvolutionSchema,
  EvolutionStageSchema,
  MigrationPlanSchema,
  MigrationStepSchema,
  RiskLevelSchema,
} from "@/schemas/evolution";

export type ArchitectureComparison = z.infer<typeof ArchitectureComparisonSchema>;
export type ComparisonChange = z.infer<typeof ComparisonChangeSchema>;
export type ComparisonValue = z.infer<typeof ComparisonValueSchema>;
export type ComponentChange = z.infer<typeof ComponentChangeSchema>;
export type ConnectionChange = z.infer<typeof ConnectionChangeSchema>;
export type FieldChange = z.infer<typeof FieldChangeSchema>;

export type RiskLevel = z.infer<typeof RiskLevelSchema>;
export type Evolution = z.infer<typeof EvolutionSchema>;
export type EvolutionStage = z.infer<typeof EvolutionStageSchema>;
export type EvolutionChange = z.infer<typeof EvolutionChangeSchema>;
export type MigrationPlan = z.infer<typeof MigrationPlanSchema>;
export type MigrationStep = z.infer<typeof MigrationStepSchema>;
