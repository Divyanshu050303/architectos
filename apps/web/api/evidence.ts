/**
 * INTEGRATION POINT: proposed contract for evidence records (spec §34).
 *   GET /evidence/{id}
 *   GET /projects/{id}/evidence   → Evidence[] (every record cited by the project's analyses)
 */
import { EvidenceListSchema, EvidenceSchema } from "@/schemas/evidence";
import type { Evidence } from "@/types/architecture";

import { apiPath, request } from "./client";

export function getEvidence(evidenceId: string, signal?: AbortSignal): Promise<Evidence> {
  return request(EvidenceSchema, { method: "GET", path: apiPath`/evidence/${evidenceId}`, signal });
}

export function listEvidence(projectId: string, signal?: AbortSignal): Promise<Evidence[]> {
  return request(EvidenceListSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/evidence`,
    signal,
  });
}
