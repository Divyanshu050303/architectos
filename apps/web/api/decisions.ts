/**
 * INTEGRATION POINT: proposed contract for Architecture Decision Records.
 *   GET  /projects/{id}/decisions
 *   POST /projects/{id}/decisions   DecisionInput → Decision
 */
import { DecisionListSchema, DecisionSchema } from "@/schemas/decisions";
import type { Decision, DecisionInput } from "@/types/architecture";

import { apiPath, request } from "./client";

export function listDecisions(projectId: string, signal?: AbortSignal): Promise<Decision[]> {
  return request(DecisionListSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/decisions`,
    signal,
  });
}

export function createDecision(projectId: string, input: DecisionInput): Promise<Decision> {
  return request(DecisionSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/decisions`,
    body: input,
  });
}
