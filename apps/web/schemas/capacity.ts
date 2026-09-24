/**
 * Capacity analysis results. Every number here is computed by the backend capacity
 * engine (engines/capacity); the frontend only formats and displays them (spec §111).
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/capacity.py.
 */
import { z } from "zod";

export const UtilizationStatusSchema = z.enum(["healthy", "warning", "critical"]);

export const ComponentUtilizationSchema = z.object({
  nodeId: z.string(),
  resource: z.string(),
  used: z.number(),
  limit: z.number(),
  unit: z.string(),
  /** used / limit */
  utilization: z.number().min(0),
  /** Warning threshold, 0..1 */
  threshold: z.number().min(0).max(1),
  status: UtilizationStatusSchema,
  evidenceId: z.string().nullable(),
});

export const EdgeThroughputSchema = z.object({
  edgeId: z.string(),
  rps: z.number(),
});

export const BottleneckSchema = z.object({
  nodeId: z.string(),
  resource: z.string(),
  description: z.string(),
  thresholdDailyActiveUsers: z.number(),
  evidenceId: z.string().nullable(),
});

export const EnvelopePointSchema = z.object({
  label: z.string(),
  dailyActiveUsers: z.number(),
  status: z.enum(["supported", "current", "warning", "exceeded"]),
});

export const CapacityAnalysisSchema = z.object({
  projectId: z.string(),
  architectureVersion: z.number().int(),
  calculatedAt: z.string(),
  load: z.object({
    dailyActiveUsers: z.number(),
    peakRps: z.number(),
    writesPerSecond: z.number(),
  }),
  utilization: z.array(ComponentUtilizationSchema),
  edges: z.array(EdgeThroughputSchema),
  bottleneck: BottleneckSchema.nullable(),
  envelope: z.object({
    maxSupportedDailyActiveUsers: z.number(),
    points: z.array(EnvelopePointSchema),
  }),
});
