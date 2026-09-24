/**
 * Drift between the saved architecture (expected) and discovered infrastructure (actual),
 * spec §45. Produced by the backend drift checker.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/drift.py.
 */
import { z } from "zod";

import { DiscoveryConnectorKindSchema } from "./discovery";
import { SeveritySchema } from "./validation";

export const DRIFT_STATUSES = ["drifted", "matching", "missing", "unexpected"] as const;
export const DriftStatusSchema = z.enum(DRIFT_STATUSES);

export const DriftItemSchema = z.object({
  id: z.string(),
  /** null for resources that exist only in the infrastructure. */
  nodeId: z.string().nullable(),
  /** e.g. "API replicas" */
  subject: z.string(),
  expected: z.string(),
  actual: z.string(),
  severity: SeveritySchema,
  status: DriftStatusSchema,
});

export const DriftReportSchema = z.object({
  checkedAt: z.string(),
  source: DiscoveryConnectorKindSchema,
  architectureVersion: z.number().int(),
  items: z.array(DriftItemSchema),
  summary: z.object({ drifted: z.number().int().min(0), matching: z.number().int().min(0) }),
});
