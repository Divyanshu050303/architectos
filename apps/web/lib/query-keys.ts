/**
 * The single TanStack Query key factory. Project-scoped keys share the
 * ["project", projectId] prefix so a whole project can be invalidated at once.
 */
export const queryKeys = {
  session: () => ["session"] as const,
  projects: () => ["projects"] as const,
  projectScope: (projectId: string) => ["project", projectId] as const,
  project: (projectId: string) => ["project", projectId, "detail"] as const,
  requirements: (projectId: string) => ["project", projectId, "requirements"] as const,
  architectureScope: (projectId: string) => ["project", projectId, "architecture"] as const,
  architecture: (projectId: string) => ["project", projectId, "architecture", "current"] as const,
  architectureVersions: (projectId: string) => ["project", projectId, "architecture", "versions"] as const,
  architectureVersion: (projectId: string, version: number) =>
    ["project", projectId, "architecture", "versions", version] as const,
  /** `from` / `to` are version numbers; lives under the architecture scope. */
  architectureComparison: (projectId: string, from: number, to: number) =>
    ["project", projectId, "architecture", "compare", from, to] as const,
  capacity: (projectId: string) => ["project", projectId, "capacity"] as const,
  reliability: (projectId: string) => ["project", projectId, "reliability"] as const,
  security: (projectId: string) => ["project", projectId, "security"] as const,
  observability: (projectId: string) => ["project", projectId, "observability"] as const,
  cost: (projectId: string) => ["project", projectId, "cost"] as const,
  drift: (projectId: string) => ["project", projectId, "drift"] as const,
  simulationScope: (projectId: string) => ["project", projectId, "simulation"] as const,
  simulationScenarios: (projectId: string) => ["project", projectId, "simulation", "scenarios"] as const,
  latestSimulation: (projectId: string) => ["project", projectId, "simulation", "latest"] as const,
  evolution: (projectId: string) => ["project", projectId, "evolution"] as const,
  /** Under the evolution prefix so refreshing the roadmap refreshes stage comparisons. */
  evolutionComparison: (projectId: string, fromStageId: string, toStageId: string) =>
    ["project", projectId, "evolution", "compare", fromStageId, toStageId] as const,
  migrations: (projectId: string) => ["project", projectId, "migrations"] as const,
  evidenceList: (projectId: string) => ["project", projectId, "evidence"] as const,
  validation: (projectId: string) => ["project", projectId, "validation"] as const,
  decisions: (projectId: string) => ["project", projectId, "decisions"] as const,
  job: (jobId: string) => ["job", jobId] as const,
  simulationRun: (runId: string) => ["simulation", runId] as const,
  migration: (migrationId: string) => ["migration", migrationId] as const,
  discoveryConnectors: () => ["discovery", "connectors"] as const,
  discoveryRun: (runId: string) => ["discovery", "run", runId] as const,
  /** Client-side cache only: there is no GET endpoint for proposals. */
  proposal: (proposalId: string) => ["proposal", proposalId] as const,
  evidence: (evidenceId: string) => ["evidence", evidenceId] as const,
};
