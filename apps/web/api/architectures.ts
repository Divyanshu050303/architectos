/**
 * INTEGRATION POINT: proposed contract for apps/api/routes/architecture.py.
 *   POST /projects/{id}/architecture/generate                → Job
 *   GET  /projects/{id}/architecture                         → Architecture | 404 "architecture_not_found"
 *   GET  /projects/{id}/architecture/versions                → ArchitectureVersionSummary[]
 *   GET  /projects/{id}/architecture/versions/{version}      → Architecture
 *   POST /projects/{id}/architecture/commands  { baseVersion, commands }  → Architecture (new version);
 *        409 "version_conflict" with details { latestVersion }
 *   PUT  /projects/{id}/architecture/layout    { baseVersion, positions } → 204 (never creates a version)
 *   GET  /projects/{id}/architecture/compare?from=<version>&to=<version> → ArchitectureComparison
 */
import type { ArchitectureCommand } from "@/features/architecture/types";
import { JobSchema } from "@/schemas/api";
import { ArchitectureSchema, ArchitectureVersionListSchema } from "@/schemas/architecture";
import { ArchitectureComparisonSchema } from "@/schemas/comparison";
import type { Architecture, ArchitectureVersionSummary, Position } from "@/types/architecture";
import type { ArchitectureComparison } from "@/types/evolution";
import type { Job } from "@/types/project";

import { apiPath, nullOnNotFound, request, requestVoid } from "./client";

export interface SaveCommandsInput {
  baseVersion: number;
  commands: ArchitectureCommand[];
}

export interface SaveLayoutInput {
  baseVersion: number;
  positions: Record<string, Position>;
}

export function generateArchitecture(projectId: string): Promise<Job> {
  return request(JobSchema, { method: "POST", path: apiPath`/projects/${projectId}/architecture/generate` });
}

export function getArchitecture(projectId: string, signal?: AbortSignal): Promise<Architecture | null> {
  return nullOnNotFound(
    request(ArchitectureSchema, {
      method: "GET",
      path: apiPath`/projects/${projectId}/architecture`,
      signal,
    }),
    "architecture_not_found",
  );
}

export function listArchitectureVersions(
  projectId: string,
  signal?: AbortSignal,
): Promise<ArchitectureVersionSummary[]> {
  return request(ArchitectureVersionListSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/architecture/versions`,
    signal,
  });
}

export function getArchitectureVersion(
  projectId: string,
  version: number,
  signal?: AbortSignal,
): Promise<Architecture> {
  return request(ArchitectureSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/architecture/versions/${version}`,
    signal,
  });
}

export function saveArchitectureCommands(projectId: string, input: SaveCommandsInput): Promise<Architecture> {
  return request(ArchitectureSchema, {
    method: "POST",
    path: apiPath`/projects/${projectId}/architecture/commands`,
    body: input,
  });
}

export function saveLayout(projectId: string, input: SaveLayoutInput): Promise<void> {
  return requestVoid({
    method: "PUT",
    path: apiPath`/projects/${projectId}/architecture/layout`,
    body: input,
  });
}

/** The diff, capacity and cost figures are produced by the backend (spec §43). */
export function compareVersions(
  projectId: string,
  fromVersion: number,
  toVersion: number,
  signal?: AbortSignal,
): Promise<ArchitectureComparison> {
  return request(ArchitectureComparisonSchema, {
    method: "GET",
    path: apiPath`/projects/${projectId}/architecture/compare`,
    query: { from: fromVersion, to: toVersion },
    signal,
  });
}
