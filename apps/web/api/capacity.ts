/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/capacity.py. All numbers
 * are computed by the backend capacity engine.
 *   GET  /projects/{id}/capacity          → CapacityAnalysis | 404 "not_analyzed"
 *   POST /projects/{id}/capacity/analyze  → CapacityAnalysis
 */
import { CapacityAnalysisSchema } from "@/schemas/capacity";
import type { CapacityAnalysis } from "@/types/capacity";

import { apiPath, nullOnNotFound, request } from "./client";

export function getCapacity(projectId: string, signal?: AbortSignal): Promise<CapacityAnalysis | null> {
  return nullOnNotFound(
    request(CapacityAnalysisSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/capacity`,
      signal,
    }),
    "not_analyzed",
  );
}

export function runCapacityAnalysis(projectId: string): Promise<CapacityAnalysis> {
  return request(CapacityAnalysisSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/capacity/analyze`,
    timeoutMs: 60_000,
  });
}
