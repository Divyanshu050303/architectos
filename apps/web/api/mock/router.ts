/**
 * MOCK BACKEND PLUMBING — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Route matching, error type and record helpers shared by the mock route modules.
 */
import { type z } from "zod";

import type { Architecture, ArchitectureNode } from "@/types/architecture";

import type { MockDbState, MockProjectRecord, MockVersionMetrics } from "./fixtures";

export interface MockRequest {
  method: string;
  /** Path relative to the API base URL, e.g. "/projects/proj_food". */
  path: string;
  body: unknown;
  /** Parsed query string; empty when there is none. */
  query?: URLSearchParams;
}

export interface MockResponse {
  status: number;
  body: unknown;
}

export class MockHttpError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details?: unknown,
  ) {
    super(message);
    this.name = "MockHttpError";
  }
}

export type Params = Record<string, string>;
export type Handler = (
  params: Params,
  body: unknown,
  db: MockDbState,
  query: URLSearchParams,
) => MockResponse;

export interface Route {
  method: string;
  pattern: RegExp;
  keys: string[];
  handler: Handler;
}

export function route(method: string, template: string, handler: Handler): Route {
  const keys: string[] = [];
  const source = template.replace(/:(\w+)/g, (_match, key: string) => {
    keys.push(key);
    return "([^/]+)";
  });
  return { method, pattern: new RegExp(`^${source}$`), keys, handler };
}

export const ok = (body: unknown, status = 200): MockResponse => ({ status, body });
export const noContent = (): MockResponse => ({ status: 204, body: null });

export function parseBody<T>(schema: z.ZodType<T>, body: unknown): T {
  const result = schema.safeParse(body);
  if (!result.success) {
    throw new MockHttpError(422, "validation_error", "The request body is invalid.", result.error.issues);
  }
  return result.data;
}

export const now = () => new Date().toISOString();

export function uniqueId(base: string, taken: ReadonlySet<string>): string {
  if (!taken.has(base)) return base;
  let i = 2;
  while (taken.has(`${base}_${i}`)) i += 1;
  return `${base}_${i}`;
}

export function replicasOf(node: ArchitectureNode | undefined): number {
  const replicas = node?.configuration.replicas;
  return typeof replicas === "number" ? replicas : 1;
}

// --- Record helpers ---------------------------------------------------------

export function findProject(db: MockDbState, projectId: string | undefined): MockProjectRecord {
  const record = db.projects.find((p) => p.id === projectId);
  if (!record) throw new MockHttpError(404, "project_not_found", `Project "${projectId}" does not exist.`);
  return record;
}

export function currentArchitecture(record: MockProjectRecord): Architecture | null {
  return record.versions.at(-1) ?? null;
}

export function requireArchitecture(record: MockProjectRecord): Architecture {
  const architecture = currentArchitecture(record);
  if (!architecture) {
    throw new MockHttpError(422, "architecture_missing", "Generate an architecture for this project first.");
  }
  return architecture;
}

export function assertBaseVersion(record: MockProjectRecord, baseVersion: number): Architecture {
  const architecture = requireArchitecture(record);
  if (architecture.version !== baseVersion) {
    throw new MockHttpError(
      409,
      "version_conflict",
      `The architecture changed: you edited v${baseVersion}, the latest is v${architecture.version}.`,
      { latestVersion: architecture.version },
    );
  }
  return architecture;
}

/** Drop every per-version analysis: a new version has not been analyzed yet ("not analyzed"). */
export function clearAnalyses(record: MockProjectRecord): void {
  record.capacity = null;
  record.validation = null;
  record.reliability = null;
  record.security = null;
  record.observability = null;
  record.cost = null;
  record.drift = null;
  record.latestSimulationId = null;
}

export function commitVersion(
  record: MockProjectRecord,
  next: Architecture,
  createdBy: Architecture["createdBy"],
  summary: string,
): Architecture {
  const version = (currentArchitecture(record)?.version ?? 0) + 1;
  const createdAt = now();
  const architecture: Architecture = {
    ...next,
    id: next.id || `arch_${record.id}`,
    projectId: record.id,
    version,
    createdAt,
    createdBy,
  };
  record.versions.push(architecture);
  record.versionSummaries.push({ version, createdAt, createdBy, summary });
  clearAnalyses(record);
  record.updatedAt = createdAt;
  return architecture;
}

/** Remember headline figures per version so comparisons can show them (fixture bookkeeping). */
export function rememberVersionMetrics(
  record: MockProjectRecord,
  version: number,
  metrics: Partial<MockVersionMetrics>,
): void {
  const key = String(version);
  record.versionMetrics[key] = {
    ...(record.versionMetrics[key] ?? { maxDailyActiveUsers: null, monthlyCost: null }),
    ...metrics,
  };
}
