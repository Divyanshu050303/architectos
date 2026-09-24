/**
 * Architecture comparison (spec §43, §93): between two saved versions or two evolution
 * stages. The diff and the capacity / cost figures are produced by the backend.
 *
 * INTEGRATION POINT: proposed contract for
 *   GET /projects/{id}/architecture/compare?from=&to=
 *   GET /projects/{id}/evolution/compare?from=&to=
 */
import { z } from "zod";

import { ComponentTypeSchema } from "./architecture";

export const ComparisonChangeSchema = z.enum(["added", "removed", "changed"]);

/** Configuration values are flattened to primitives for display. */
export const ComparisonValueSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

export const FieldChangeSchema = z.object({
  field: z.string(),
  before: ComparisonValueSchema,
  after: ComparisonValueSchema,
});

export const ComponentChangeSchema = z.object({
  change: ComparisonChangeSchema,
  nodeId: z.string(),
  name: z.string(),
  type: ComponentTypeSchema,
  details: z.array(FieldChangeSchema),
});

export const ConnectionChangeSchema = z.object({
  change: ComparisonChangeSchema,
  edgeId: z.string(),
  sourceName: z.string(),
  targetName: z.string(),
  details: z.array(FieldChangeSchema),
});

export const ComparisonSideSchema = z.object({
  /** e.g. "v3" or "V2 · 5M DAU" */
  label: z.string(),
  version: z.number().int().nullable(),
});

export const ArchitectureComparisonSchema = z.object({
  from: ComparisonSideSchema,
  to: ComparisonSideSchema,
  components: z.array(ComponentChangeSchema),
  connections: z.array(ConnectionChangeSchema),
  capacity: z.object({
    beforeMaxDailyActiveUsers: z.number().nullable(),
    afterMaxDailyActiveUsers: z.number().nullable(),
  }),
  cost: z.object({
    before: z.number().nullable(),
    after: z.number().nullable(),
    currency: z.string(),
  }),
});
