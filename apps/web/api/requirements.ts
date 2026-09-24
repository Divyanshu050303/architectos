/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/requirements.py.
 *   GET /projects/{id}/requirements
 *   PUT /projects/{id}/requirements   { description, functional, nonFunctional }
 */
import { RequirementsSchema } from "@/schemas/requirements";
import type { Requirements } from "@/types/project";

import { apiPath, request } from "./client";

export type RequirementsInput = Pick<Requirements, "description" | "functional" | "nonFunctional">;

export function getRequirements(projectId: string, signal?: AbortSignal): Promise<Requirements> {
  return request(RequirementsSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/requirements`,
    signal,
  });
}

export function saveRequirements(projectId: string, input: RequirementsInput): Promise<Requirements> {
  return request(RequirementsSchema, {
    method: "PUT",
    path: apiPath`/projects/${projectId}/requirements`,
    body: input,
  });
}
