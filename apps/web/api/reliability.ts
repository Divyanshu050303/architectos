/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/reliability.py. All figures
 * are computed by the backend reliability engine.
 *   GET  /projects/{id}/reliability          → ReliabilityAnalysis | 404 "not_analyzed"
 *   POST /projects/{id}/reliability/analyze  → ReliabilityAnalysis
 */
import { ReliabilityAnalysisSchema } from "@/schemas/reliability";
import type { ReliabilityAnalysis } from "@/types/reliability";

import { apiPath, nullOnNotFound, request } from "./client";

export function getReliability(projectId: string, signal?: AbortSignal): Promise<ReliabilityAnalysis | null> {
  return nullOnNotFound(
    request(ReliabilityAnalysisSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/reliability`,
      signal,
    }),
    "not_analyzed",
  );
}

export function runReliabilityAnalysis(projectId: string): Promise<ReliabilityAnalysis> {
  return request(ReliabilityAnalysisSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/reliability/analyze`,
    timeoutMs: 60_000,
  });
}
