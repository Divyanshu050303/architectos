/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/cost.py (spec §71). All
 * amounts come from the backend cost engine and provider price lists.
 *   GET  /projects/{id}/cost            → CostEstimate | 404 "not_calculated"
 *   POST /projects/{id}/cost/calculate  → CostEstimate
 */
import { CostEstimateSchema } from "@/schemas/cost";
import type { CostEstimate } from "@/types/cost";

import { apiPath, nullOnNotFound, request } from "./client";

export function getCost(projectId: string, signal?: AbortSignal): Promise<CostEstimate | null> {
  return nullOnNotFound(
    request(CostEstimateSchema, { method: "GET", path: apiPath`/projects/${projectId}/cost`, signal }),
    "not_calculated",
  );
}

export function calculateCost(projectId: string): Promise<CostEstimate> {
  return request(CostEstimateSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/cost/calculate`,
    timeoutMs: 60_000,
  });
}
