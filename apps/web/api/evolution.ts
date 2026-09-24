/**
 * INTEGRATION POINT: proposed contract for the evolution planner (spec §42–43).
 *   GET /projects/{id}/evolution                              → Evolution | 404 "evolution_not_found"
 *   GET /projects/{id}/evolution/compare?from=<stageId>&to=<stageId> → ArchitectureComparison
 */
import { ArchitectureComparisonSchema } from "@/schemas/comparison";
import { EvolutionSchema } from "@/schemas/evolution";
import type { ArchitectureComparison, Evolution } from "@/types/evolution";

import { apiPath, nullOnNotFound, request } from "./client";

/** Resolves to null when the project has no roadmap yet (404 or no stages). */
export async function getEvolution(projectId: string, signal?: AbortSignal): Promise<Evolution | null> {
  const evolution = await nullOnNotFound(
    request(EvolutionSchema, { method: "GET", path: apiPath`/projects/${projectId}/evolution`, signal }),
    "evolution_not_found",
  );
  return evolution && evolution.stages.length > 0 ? evolution : null;
}

export function compareStages(
  projectId: string,
  fromStageId: string,
  toStageId: string,
  signal?: AbortSignal,
): Promise<ArchitectureComparison> {
  return request(ArchitectureComparisonSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/evolution/compare`,
    query: { from: fromStageId, to: toStageId },
    signal,
  });
}
