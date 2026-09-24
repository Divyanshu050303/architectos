/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/simulations.py (spec §40–41, §72).
 * Simulations run as jobs; every result is produced by the backend simulation engine.
 *   GET  /projects/{id}/simulation/scenarios   → SimulationScenario[]
 *   POST /projects/{id}/simulations            SimulationConfig → SimulationRun (202, queued/running)
 *   GET  /projects/{id}/simulations/latest     → SimulationRun | 404 "simulation_not_found"
 *   GET  /simulations/{runId}                  → SimulationRun
 */
import { SimulationRunSchema, SimulationScenarioListSchema } from "@/schemas/simulation";
import type { SimulationConfig, SimulationRun, SimulationScenario } from "@/types/simulation";

import { apiPath, nullOnNotFound, request } from "./client";

export function listSimulationScenarios(
  projectId: string,
  signal?: AbortSignal,
): Promise<SimulationScenario[]> {
  return request(SimulationScenarioListSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/simulation/scenarios`,
    signal,
  });
}

export function startSimulation(projectId: string, config: SimulationConfig): Promise<SimulationRun> {
  return request(SimulationRunSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/simulations`,
    body: config,
  });
}

export function getSimulationRun(runId: string, signal?: AbortSignal): Promise<SimulationRun> {
  return request(SimulationRunSchema, { method: "GET", path: apiPath`/simulations/${runId}`, signal });
}

export function getLatestSimulation(projectId: string, signal?: AbortSignal): Promise<SimulationRun | null> {
  return nullOnNotFound(
    request(SimulationRunSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/simulations/latest`,
      signal,
    }),
    "simulation_not_found",
  );
}
