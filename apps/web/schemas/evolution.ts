/**
 * Architecture evolution roadmap (spec §42) and migration plans between stages.
 * Produced by the backend planner; the frontend only renders it.
 *
 * INTEGRATION POINT: proposed contract for
 *   GET /projects/{id}/evolution, GET /projects/{id}/migrations, GET /migrations/{id}
 */
import { z } from "zod";

export const RiskLevelSchema = z.enum(["low", "medium", "high"]);

export const EvolutionChangeSchema = z.object({
  kind: z.enum(["add", "remove", "change"]),
  description: z.string(),
});

export const EvolutionStageSchema = z.object({
  id: z.string(),
  /** e.g. "V1" */
  label: z.string(),
  dailyActiveUsers: z.number(),
  status: z.enum(["past", "current", "planned"]),
  /** The saved version this stage corresponds to; null for planned stages. */
  architectureVersion: z.number().int().nullable(),
  trigger: z.string(),
  changes: z.array(EvolutionChangeSchema),
  monthlyCost: z.number().min(0),
  risk: RiskLevelSchema,
  /** Migration that reaches this stage, if one is planned. */
  migrationId: z.string().nullable(),
  maxSupportedDailyActiveUsers: z.number(),
});

export const EvolutionSchema = z.object({
  stages: z.array(EvolutionStageSchema),
});

export const MigrationStepSchema = z.object({
  id: z.string(),
  order: z.number().int().positive(),
  title: z.string(),
  description: z.string(),
  /** Step ids that must be done first. */
  dependsOn: z.array(z.string()),
  risk: RiskLevelSchema,
  rollback: z.string(),
  /** Human-readable, e.g. "2 days" */
  estimatedDuration: z.string(),
  nodeIds: z.array(z.string()),
  status: z.enum(["pending", "in_progress", "done"]),
});

export const MigrationPlanSchema = z.object({
  id: z.string(),
  title: z.string(),
  fromStageId: z.string(),
  toStageId: z.string(),
  status: z.enum(["draft", "in_progress", "completed"]),
  overallRisk: RiskLevelSchema,
  estimatedDuration: z.string(),
  rollbackPlan: z.string(),
  steps: z.array(MigrationStepSchema),
});

export const MigrationPlanListSchema = z.array(MigrationPlanSchema);
