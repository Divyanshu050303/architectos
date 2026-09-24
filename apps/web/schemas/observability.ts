/**
 * Observability coverage, SLOs and gaps (spec §37, §68). Produced by the backend; the
 * frontend only renders it.
 *
 * INTEGRATION POINT: proposed contract for apps/api/routes/observability.py.
 */
import { z } from "zod";

export const TELEMETRY_SIGNALS = ["metrics", "logs", "traces", "alerts", "dashboards"] as const;
export const TelemetrySignalSchema = z.enum(TELEMETRY_SIGNALS);

export const ObservabilityCoverageSchema = z.object({
  nodeId: z.string(),
  metrics: z.boolean(),
  logs: z.boolean(),
  traces: z.boolean(),
  alerts: z.boolean(),
  dashboards: z.boolean(),
});

export const SloSchema = z.object({
  id: z.string(),
  name: z.string(),
  nodeIds: z.array(z.string()),
  /** Fractions, e.g. 0.999 */
  target: z.number().min(0).max(1),
  current: z.number().min(0).max(1),
  /** 0..1 of the window's error budget still unspent. */
  errorBudgetRemaining: z.number().min(0).max(1),
  /** e.g. "30d" */
  window: z.string(),
});

export const ObservabilityGapSchema = z.object({
  nodeId: z.string(),
  missing: z.array(TelemetrySignalSchema),
  recommendation: z.string(),
});

export const ObservabilityAnalysisSchema = z.object({
  score: z.number().min(0).max(100),
  coverage: z.array(ObservabilityCoverageSchema),
  slos: z.array(SloSchema),
  gaps: z.array(ObservabilityGapSchema),
  analyzedAt: z.string(),
  architectureVersion: z.number().int(),
});
