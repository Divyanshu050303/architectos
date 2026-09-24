/**
 * Validation findings and explainable health (spec §37–39). Produced by the backend
 * validation engine (engines/validation).
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/validation.py.
 */
import { z } from "zod";

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export const SeveritySchema = z.enum(SEVERITIES);

export const HEALTH_CATEGORIES = ["capacity", "reliability", "security", "observability", "cost"] as const;
export const HealthCategorySchema = z.enum(HEALTH_CATEGORIES);

export const FindingSchema = z.object({
  id: z.string(),
  ruleId: z.string(),
  category: HealthCategorySchema,
  severity: SeveritySchema,
  title: z.string(),
  /** Short location, e.g. "API → Payment Service" */
  location: z.string(),
  whyItMatters: z.string(),
  recommendation: z.string(),
  nodeIds: z.array(z.string()),
  edgeIds: z.array(z.string()),
  evidenceIds: z.array(z.string()),
  /** The backend can produce a proposal that fixes it. */
  fixable: z.boolean(),
  status: z.enum(["open", "ignored"]),
});

export const HealthCategoryScoreSchema = z.object({
  category: HealthCategorySchema,
  score: z.number().min(0).max(100),
  findingIds: z.array(z.string()),
  summary: z.string(),
});

export const ValidationReportSchema = z.object({
  projectId: z.string(),
  architectureVersion: z.number().int(),
  validatedAt: z.string(),
  findings: z.array(FindingSchema),
  health: z.object({
    overall: z.number().min(0).max(100),
    categories: z.array(HealthCategoryScoreSchema),
  }),
});
