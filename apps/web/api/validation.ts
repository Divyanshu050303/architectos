/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/validation.py.
 *   GET   /projects/{id}/validation                        → ValidationReport | 404 "not_validated"
 *   POST  /projects/{id}/validation/run                    → ValidationReport
 *   PATCH /projects/{id}/validation/findings/{findingId}   { status } → Finding
 */
import { FindingSchema, ValidationReportSchema } from "@/schemas/validation";
import type { Finding, ValidationReport } from "@/types/validation";

import { apiPath, nullOnNotFound, request } from "./client";

export function getValidation(projectId: string, signal?: AbortSignal): Promise<ValidationReport | null> {
  return nullOnNotFound(
    request(ValidationReportSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/validation`,
      signal,
    }),
    "not_validated",
  );
}

export function runValidation(projectId: string): Promise<ValidationReport> {
  return request(ValidationReportSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/validation/run`,
    timeoutMs: 60_000,
  });
}

export function setFindingStatus(
  projectId: string,
  findingId: string,
  status: Finding["status"],
): Promise<Finding> {
  return request(FindingSchema, {
    method: "PATCH",
    path: apiPath`/projects/${projectId}/validation/findings/${findingId}`,
    body: { status },
  });
}
