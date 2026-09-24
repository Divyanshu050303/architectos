/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/discovery.py (spec §44).
 * Discovery runs as a job; resource mapping and the proposed architecture come from the backend.
 *   GET  /discovery/connectors                 → DiscoveryConnector[]
 *   POST /projects/{id}/discoveries            { connector, options } → DiscoveryRun (202);
 *        422 "connector_not_connected"
 *   GET  /discoveries/{runId}                  → DiscoveryRun
 *   POST /discoveries/{runId}/save             { baseVersion | null } → Architecture (createdBy "discovery");
 *        409 "version_conflict" with details { latestVersion }, 409 "discovery_not_ready"
 */
import { ArchitectureSchema } from "@/schemas/architecture";
import { DiscoveryConnectorListSchema, DiscoveryRunSchema } from "@/schemas/discovery";
import type { Architecture } from "@/types/architecture";
import type { DiscoveryConnector, DiscoveryRequest, DiscoveryRun } from "@/types/discovery";

import { apiPath, request } from "./client";

export function listConnectors(signal?: AbortSignal): Promise<DiscoveryConnector[]> {
  return request(DiscoveryConnectorListSchema, { method: "GET", path: "/discovery/connectors", signal });
}

export function startDiscovery(projectId: string, input: DiscoveryRequest): Promise<DiscoveryRun> {
  return request(DiscoveryRunSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/discoveries`,
    body: input,
  });
}

export function getDiscoveryRun(runId: string, signal?: AbortSignal): Promise<DiscoveryRun> {
  return request(DiscoveryRunSchema, { method: "GET", path: apiPath`/discoveries/${runId}`, signal });
}

export function saveDiscovery(runId: string, baseVersion: number | null): Promise<Architecture> {
  return request(ArchitectureSchema, {
    method: "POST",
    path: apiPath`/discoveries/${runId}/save`,
    body: { baseVersion },
  });
}
