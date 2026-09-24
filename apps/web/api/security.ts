/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/security.py. Exposure,
 * controls and threats are produced by the backend security rules.
 *   GET  /projects/{id}/security          → SecurityAnalysis | 404 "not_analyzed"
 *   POST /projects/{id}/security/analyze  → SecurityAnalysis
 */
import { SecurityAnalysisSchema } from "@/schemas/security";
import type { SecurityAnalysis } from "@/types/security";

import { apiPath, nullOnNotFound, request } from "./client";

export function getSecurity(projectId: string, signal?: AbortSignal): Promise<SecurityAnalysis | null> {
  return nullOnNotFound(
    request(SecurityAnalysisSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/security`,
      signal,
    }),
    "not_analyzed",
  );
}

export function runSecurityAnalysis(projectId: string): Promise<SecurityAnalysis> {
  return request(SecurityAnalysisSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/security/analyze`,
    timeoutMs: 60_000,
  });
}
