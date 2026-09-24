/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/drift.py (spec §45).
 *   GET  /projects/{id}/drift         → DriftReport | 404 "not_checked"
 *   POST /projects/{id}/drift/check   → DriftReport; 422 "discovery_required" without a connected source
 */
import { DriftReportSchema } from "@/schemas/drift";
import type { DriftReport } from "@/types/discovery";

import { apiPath, nullOnNotFound, request } from "./client";

export function getDrift(projectId: string, signal?: AbortSignal): Promise<DriftReport | null> {
  return nullOnNotFound(
    request(DriftReportSchema, { method: "GET", path: apiPath`/projects/${projectId}/drift`, signal }),
    "not_checked",
  );
}

export function checkDrift(projectId: string): Promise<DriftReport> {
  return request(DriftReportSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/drift/check`,
    timeoutMs: 60_000,
  });
}
