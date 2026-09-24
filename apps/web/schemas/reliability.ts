/**
 * Reliability analysis (spec §37, §70): availability estimate, single points of failure,
 * critical paths and cascade risks. Computed by the backend reliability engine; the
 * frontend only renders it.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/reliability.py.
 */
import { z } from "zod";

import { SeveritySchema } from "./validation";

/** Availability values are fractions, e.g. 0.9995. */
const Availability = z.number().min(0).max(1);

export const SinglePointOfFailureSchema = z.object({
  nodeId: z.string(),
  reason: z.string(),
  dependentNodeIds: z.array(z.string()),
  severity: SeveritySchema,
  evidenceId: z.string().nullable(),
});

export const CriticalPathSchema = z.object({
  nodeIds: z.array(z.string()),
  availability: Availability,
});

export const CascadeRiskSchema = z.object({
  edgeId: z.string(),
  reasons: z.array(z.string()),
});

export const ReliabilityAnalysisSchema = z.object({
  availability: z.object({
    target: Availability.nullable(),
    estimated: Availability,
    monthlyDowntimeMinutes: z.number().min(0),
  }),
  entrypoints: z.array(z.object({ nodeId: z.string(), availability: Availability })),
  singlePointsOfFailure: z.array(SinglePointOfFailureSchema),
  criticalPaths: z.array(CriticalPathSchema),
  cascadeRisks: z.array(CascadeRiskSchema),
  criticalEdgeIds: z.array(z.string()),
  analyzedAt: z.string(),
  architectureVersion: z.number().int(),
});
