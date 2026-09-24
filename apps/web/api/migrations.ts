/**
 * INTEGRATION POINT: proposed contract for migration plans between evolution stages.
 *   GET /projects/{id}/migrations   → MigrationPlan[]
 *   GET /migrations/{id}            → MigrationPlan
 */
import { MigrationPlanListSchema, MigrationPlanSchema } from "@/schemas/evolution";
import type { MigrationPlan } from "@/types/evolution";

import { apiPath, request } from "./client";

export function listMigrations(projectId: string, signal?: AbortSignal): Promise<MigrationPlan[]> {
  return request(MigrationPlanListSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/migrations`,
    signal,
  });
}

export function getMigration(migrationId: string, signal?: AbortSignal): Promise<MigrationPlan> {
  return request(MigrationPlanSchema, { method: "GET", path: apiPath`/migrations/${migrationId}`, signal });
}
