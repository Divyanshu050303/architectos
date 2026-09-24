/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/observability.py.
 *   GET  /projects/{id}/observability          → ObservabilityAnalysis | 404 "not_analyzed"
 *   POST /projects/{id}/observability/analyze  → ObservabilityAnalysis
 */
import { ObservabilityAnalysisSchema } from "@/schemas/observability";
import type { ObservabilityAnalysis } from "@/types/observability";

import { apiPath, nullOnNotFound, request } from "./client";

export function getObservability(
  projectId: string,
  signal?: AbortSignal,
): Promise<ObservabilityAnalysis | null> {
  return nullOnNotFound(
    request(ObservabilityAnalysisSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/observability`,
      signal,
    }),
    "not_analyzed",
  );
}

export function runObservabilityAnalysis(projectId: string): Promise<ObservabilityAnalysis> {
  return request(ObservabilityAnalysisSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/observability/analyze`,
    timeoutMs: 60_000,
  });
}
