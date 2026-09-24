/**
 * MOCK BACKEND ROUTES (extended surfaces) — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Reliability, security, observability, cost, drift, simulation, comparison, evolution,
 * migration, discovery and evidence-list routes over the in-memory mock db. Everything
 * here is fixture data or a simple template derivation of it (graph walks, filtering to
 * the nodes that still exist, per-type price tables). It is NOT real analysis: the real
 * results must come from the backend engines, and the frontend never computes them.
 */
import { createId } from "@/lib/utils";
import { DiscoveryRequestSchema, DiscoverySaveRequestSchema } from "@/schemas/discovery";
import { SimulationConfigSchema } from "@/schemas/simulation";
import type { Architecture, ArchitectureNode, Evidence } from "@/types/architecture";
import type { CostEstimate, NodeCost } from "@/types/cost";
import type { DiscoveryRun, DriftReport } from "@/types/discovery";
import type {
  ArchitectureComparison,
  ComparisonValue,
  ComponentChange,
  ConnectionChange,
  EvolutionStage,
  FieldChange,
} from "@/types/evolution";
import type { ObservabilityAnalysis, ObservabilityCoverage } from "@/types/observability";
import type { ReliabilityAnalysis, SinglePointOfFailure } from "@/types/reliability";
import type { Exposure, SecurityAnalysis, SecurityControls, TrustBoundary } from "@/types/security";
import type {
  SimulationConfig,
  SimulationImpact,
  SimulationResult,
  SimulationRun,
  SimulationScenario,
  SimulationScenarioKind,
  SimulationTimelineEvent,
} from "@/types/simulation";

import type { MockDbState, MockDiscoveryRecord, MockProjectRecord, MockSimulationRecord } from "./fixtures";
import {
  COST_ASSUMPTIONS,
  COST_CATEGORY_BY_TYPE,
  driftSummary,
  observabilityGaps,
} from "./fixtures-analysis";
import {
  commitVersion,
  currentArchitecture,
  findProject,
  MockHttpError,
  now,
  ok,
  parseBody,
  rememberVersionMetrics,
  replicasOf,
  requireArchitecture,
  type Route,
  route,
} from "./router";

const clamp = (value: number, min: number, max: number) => Math.min(max, Math.max(min, value));
const MINUTES_PER_MONTH = 43_800;

function nodeMap(architecture: Architecture): Map<string, ArchitectureNode> {
  return new Map(architecture.nodes.map((n) => [n.id, n]));
}

function names(architecture: Architecture, ids: readonly string[]): string {
  const byId = nodeMap(architecture);
  const list = ids.map((id) => byId.get(id)?.name ?? id);
  return list.length <= 3 ? list.join(", ") : `${list.slice(0, 3).join(", ")} and ${list.length - 3} more`;
}

/**
 * Mock graph walk over hard (synchronous + critical) edges, level by level. "upstream"
 * follows callers of the start nodes (who breaks when they fail); "downstream" follows
 * callees (where extra load lands). Client nodes are not components and are skipped.
 */
function hardWalk(
  architecture: Architecture,
  start: readonly string[],
  direction: "upstream" | "downstream",
): string[][] {
  const byId = nodeMap(architecture);
  const visited = new Set(start);
  const levels: string[][] = [[...start]];
  let frontier = new Set(start);
  while (frontier.size > 0) {
    const next = new Set<string>();
    for (const edge of architecture.edges) {
      if (!edge.synchronous || !edge.critical) continue;
      const [from, to] = direction === "upstream" ? [edge.target, edge.source] : [edge.source, edge.target];
      if (!frontier.has(from) || visited.has(to) || byId.get(to)?.type === "client") continue;
      visited.add(to);
      next.add(to);
    }
    if (next.size > 0) levels.push([...next]);
    frontier = next;
  }
  return levels;
}

// --- Reliability (mock) -----------------------------------------------------

function mockReliability(record: MockProjectRecord, architecture: Architecture): ReliabilityAnalysis {
  const byId = nodeMap(architecture);
  const edgeIds = new Set(architecture.edges.map((e) => e.id));
  const baseline = record.baselineReliability;
  const stillSpof = (nodeId: string) => {
    const node = byId.get(nodeId);
    return node !== undefined && replicasOf(node) < 2;
  };

  const kept = (baseline?.singlePointsOfFailure ?? []).filter((s) => stillSpof(s.nodeId));
  const keptIds = new Set(kept.map((s) => s.nodeId));
  const added: SinglePointOfFailure[] = architecture.nodes
    .filter((n) => n.type === "database" && replicasOf(n) < 2 && !keptIds.has(n.id))
    .map((n) => ({
      nodeId: n.id,
      reason: "Single instance with no replica (mock rule).",
      dependentNodeIds: hardWalk(architecture, [n.id], "upstream").slice(1).flat(),
      severity: "critical",
      evidenceId: null,
    }));
  const spofs = [...kept, ...added].map((s) => ({
    ...s,
    dependentNodeIds: s.dependentNodeIds.filter((id) => byId.has(id)),
  }));
  const removed = (baseline?.singlePointsOfFailure.length ?? 0) - kept.length;
  const estimated = clamp(
    baseline
      ? baseline.availability.estimated + 0.0008 * removed - 0.0008 * added.length
      : 0.9995 - 0.0007 * spofs.length,
    0.9,
    0.99999,
  );
  const spofIds = new Set(spofs.map((s) => s.nodeId));
  const targets = new Set(architecture.edges.map((e) => e.target));
  const entrypoints =
    baseline?.entrypoints.filter((e) => byId.has(e.nodeId)) ??
    architecture.nodes
      .filter(
        (n) =>
          architecture.edges.some((e) => e.target === n.id && byId.get(e.source)?.type === "client") ||
          (!targets.has(n.id) && !["client", "observability", "external"].includes(n.type)),
      )
      .map((n) => ({ nodeId: n.id, availability: estimated }));

  return {
    availability: {
      target: record.requirements.nonFunctional.availabilityTarget ?? baseline?.availability.target ?? null,
      estimated,
      monthlyDowntimeMinutes: Math.round(MINUTES_PER_MONTH * (1 - estimated)),
    },
    entrypoints: entrypoints.map((e) => (baseline ? e : { ...e, availability: estimated })),
    singlePointsOfFailure: spofs,
    criticalPaths: (baseline?.criticalPaths ?? []).filter((p) => p.nodeIds.every((id) => byId.has(id))),
    cascadeRisks: [
      ...(baseline?.cascadeRisks ?? []).filter((c) => {
        const edge = architecture.edges.find((e) => e.id === c.edgeId);
        if (!edge) return false;
        const dependsOnSpof = c.reasons.some((r) => r.includes("single-instance"));
        return !dependsOnSpof || spofIds.has(edge.target);
      }),
      ...architecture.edges
        .filter(
          (e) =>
            e.synchronous &&
            e.critical &&
            added.some((s) => s.nodeId === e.target) &&
            !(baseline?.cascadeRisks ?? []).some((c) => c.edgeId === e.id),
        )
        .map((e) => ({ edgeId: e.id, reasons: ["Depends on a single-instance database"] })),
    ].filter((c) => edgeIds.has(c.edgeId)),
    criticalEdgeIds: architecture.edges.filter((e) => e.synchronous && e.critical).map((e) => e.id),
    analyzedAt: now(),
    architectureVersion: architecture.version,
  };
}

// --- Security (mock) --------------------------------------------------------

function templateExposure(node: ArchitectureNode): Exposure {
  switch (node.type) {
    case "client":
    case "cdn":
    case "load_balancer":
    case "gateway":
      return { nodeId: node.id, level: "public", reason: "Internet-facing (mock rule by component type)." };
    case "external":
      return { nodeId: node.id, level: "public", reason: "Reached over public egress (mock rule)." };
    case "database":
    case "cache":
    case "queue":
    case "storage":
      return { nodeId: node.id, level: "private", reason: "Data store in a private subnet (mock rule)." };
    default:
      return { nodeId: node.id, level: "internal", reason: "Reachable inside the network only (mock rule)." };
  }
}

function templateControls(node: ArchitectureNode): SecurityControls {
  return {
    nodeId: node.id,
    authentication: null,
    authorization: null,
    encryptionInTransit: null,
    encryptionAtRest: null,
    secretsManagement: null,
    handlesPii: false,
  };
}

function mockSecurity(record: MockProjectRecord, architecture: Architecture): SecurityAnalysis {
  const byId = nodeMap(architecture);
  const baseline = record.baselineSecurity;
  const keep = <T extends { nodeId: string }>(rows: readonly T[] | undefined) =>
    (rows ?? []).filter((r) => byId.has(r.nodeId));
  const exposure = keep(baseline?.exposure);
  const controls = keep(baseline?.controls);
  const covered = new Set(exposure.map((e) => e.nodeId));
  const controlled = new Set(controls.map((c) => c.nodeId));
  const newNodes = architecture.nodes.filter((n) => !covered.has(n.id));

  let trustBoundaries: TrustBoundary[];
  if (baseline) {
    trustBoundaries = baseline.trustBoundaries
      .map((b) => ({ ...b, nodeIds: b.nodeIds.filter((id) => byId.has(id)) }))
      .filter((b) => b.nodeIds.length > 0);
  } else {
    const byDomain = new Map<string, string[]>();
    for (const node of architecture.nodes) {
      const domain = node.domain ?? "default";
      byDomain.set(domain, [...(byDomain.get(domain) ?? []), node.id]);
    }
    trustBoundaries = [...byDomain].map(([domain, nodeIds]) => ({
      id: `tb_${domain}`,
      name: domain.charAt(0).toUpperCase() + domain.slice(1),
      nodeIds,
    }));
  }
  const newPublic = newNodes.filter((n) => templateExposure(n).level === "public").length;
  return {
    score: clamp((baseline?.score ?? 80) - 2 * newPublic, 0, 100),
    trustBoundaries,
    exposure: [...exposure, ...newNodes.map(templateExposure)],
    controls: [
      ...controls,
      ...architecture.nodes
        .filter((n) => !controlled.has(n.id) && n.type !== "client" && n.type !== "external")
        .map(templateControls),
    ],
    threats: (baseline?.threats ?? []).filter((t) => t.nodeIds.every((id) => byId.has(id))),
    analyzedAt: now(),
    architectureVersion: architecture.version,
  };
}

// --- Observability (mock) ---------------------------------------------------

function coverageFraction(rows: readonly ObservabilityCoverage[]): number {
  if (rows.length === 0) return 0;
  const on = rows.reduce(
    (sum, r) => sum + [r.metrics, r.logs, r.traces, r.alerts, r.dashboards].filter(Boolean).length,
    0,
  );
  return on / (rows.length * 5);
}

function mockObservability(record: MockProjectRecord, architecture: Architecture): ObservabilityAnalysis {
  const byId = nodeMap(architecture);
  const baseline = record.baselineObservability;
  const kept = (baseline?.coverage ?? []).filter((c) => byId.has(c.nodeId));
  const covered = new Set(kept.map((c) => c.nodeId));
  const scraped = architecture.nodes.some((n) => n.type === "observability");
  const coverage = [
    ...kept,
    ...architecture.nodes
      .filter((n) => !covered.has(n.id) && n.type !== "client" && n.type !== "external")
      .map((n) => ({
        nodeId: n.id,
        metrics: scraped,
        logs: false,
        traces: false,
        alerts: false,
        dashboards: false,
      })),
  ];
  const fraction = coverageFraction(coverage);
  const score = baseline
    ? baseline.score + (fraction - coverageFraction(baseline.coverage)) * 100
    : fraction * 100;
  return {
    score: Math.round(clamp(score, 0, 100)),
    coverage,
    slos: (baseline?.slos ?? []).filter((s) => s.nodeIds.every((id) => byId.has(id))),
    gaps: observabilityGaps(coverage),
    analyzedAt: now(),
    architectureVersion: architecture.version,
  };
}

// --- Cost (mock price table) ------------------------------------------------

const MOCK_PRICE_PER_REPLICA: Partial<Record<ArchitectureNode["type"], [string, number]>> = {
  service: ["Container task (2 vCPU, 4 GB)", 72],
  worker: ["Container task (1 vCPU, 2 GB)", 36],
  gateway: ["Managed API gateway", 45],
  load_balancer: ["Load balancer", 38],
  cdn: ["CDN distribution", 64],
  database: ["Database instance", 184],
  cache: ["Cache node (8 GB)", 37],
  queue: ["Managed queue", 150],
  storage: ["Object storage", 23],
  observability: ["Managed monitoring", 48],
};

function mockCost(record: MockProjectRecord, architecture: Architecture): CostEstimate {
  const baseline = record.baselineCost;
  const baselineArchitecture = record.versions.find((v) => v.version === baseline?.architectureVersion);
  const baselineNodes = baselineArchitecture
    ? nodeMap(baselineArchitecture)
    : new Map<string, ArchitectureNode>();
  const baselineRows = new Map((baseline?.nodes ?? []).map((n) => [n.nodeId, n]));

  const nodes: NodeCost[] = architecture.nodes.flatMap((node): NodeCost[] => {
    const previous = baselineRows.get(node.id);
    if (previous) {
      const ratio = replicasOf(node) / replicasOf(baselineNodes.get(node.id));
      if (ratio === 1) return [previous];
      const breakdown = previous.breakdown.map((b) => ({
        item: `${b.item} (scaled to ${replicasOf(node)} replicas)`,
        monthly: Math.round(b.monthly * ratio),
      }));
      return [{ nodeId: node.id, monthly: breakdown.reduce((s, b) => s + b.monthly, 0), breakdown }];
    }
    const price = MOCK_PRICE_PER_REPLICA[node.type];
    if (!price) return [];
    const replicas = replicasOf(node);
    const monthly = price[1] * replicas;
    return [{ nodeId: node.id, monthly, breakdown: [{ item: `${replicas} × ${price[0]}`, monthly }] }];
  });

  const types = new Map(architecture.nodes.map((n) => [n.id, n.type]));
  const categories = new Map<string, number>();
  for (const row of nodes) {
    const type = types.get(row.nodeId);
    const category = (type && COST_CATEGORY_BY_TYPE[type]) ?? "Other";
    categories.set(category, (categories.get(category) ?? 0) + row.monthly);
  }
  const total = nodes.reduce((sum, n) => sum + n.monthly, 0);
  rememberVersionMetrics(record, architecture.version, { monthlyCost: total });
  return {
    provider: baseline?.provider ?? "aws",
    currency: baseline?.currency ?? "USD",
    period: "month",
    total,
    nodes,
    byCategory: [...categories]
      .map(([category, monthly]) => ({ category, monthly }))
      .sort((a, b) => b.monthly - a.monthly),
    assumptions: baseline?.assumptions ?? COST_ASSUMPTIONS,
    evidenceIds: (baseline?.evidenceIds ?? []).filter(
      (id) => id !== "ev_cost_total" || total === baseline?.total,
    ),
    calculatedAt: now(),
    architectureVersion: architecture.version,
  };
}

// --- Drift (mock) -----------------------------------------------------------

function mockDrift(record: MockProjectRecord, architecture: Architecture): DriftReport {
  const baseline = record.baselineDrift;
  if (!baseline) {
    throw new MockHttpError(
      422,
      "discovery_required",
      "Connect a discovery source and import it before checking drift.",
    );
  }
  const byId = nodeMap(architecture);
  const changed = baseline.architectureVersion !== architecture.version;
  const items = baseline.items
    .filter((item) => item.nodeId === null || byId.has(item.nodeId))
    .map((item) => {
      const node = item.nodeId === null ? undefined : byId.get(item.nodeId);
      if (!changed || !node || !/replicas$/i.test(item.subject)) return item;
      // The saved architecture changed: re-read the expected replica count from it.
      const expected = String(replicasOf(node));
      const matching = expected === item.actual;
      return {
        ...item,
        expected,
        status: matching ? ("matching" as const) : ("drifted" as const),
        severity: matching
          ? ("info" as const)
          : item.severity === "info"
            ? ("medium" as const)
            : item.severity,
      };
    });
  return {
    checkedAt: now(),
    source: baseline.source,
    architectureVersion: architecture.version,
    items,
    summary: driftSummary(items),
  };
}

// --- Simulation (mock) ------------------------------------------------------

function scenariosFor(record: MockProjectRecord, architecture: Architecture | null): SimulationScenario[] {
  if (!architecture) return [];
  const byId = nodeMap(architecture);
  if (record.scenarios) {
    return record.scenarios
      .map((s) => ({ ...s, targetNodeIds: s.targetNodeIds.filter((id) => byId.has(id)) }))
      .filter((s) => s.targetNodeIds.length > 0);
  }
  const ofType = (type: ArchitectureNode["type"]) =>
    architecture.nodes.filter((n) => n.type === type).slice(0, 3);
  const failure = (
    kind: SimulationScenarioKind,
    node: ArchitectureNode,
    what: string,
  ): SimulationScenario => ({
    id: `scn_${kind}_${node.id}`,
    kind,
    label: `${node.name} failure`,
    description: `${node.name} ${what} for the whole run.`,
    targetNodeIds: [node.id],
  });
  const entry = ofType("load_balancer")[0] ?? ofType("gateway")[0] ?? ofType("service")[0];
  return [
    ...ofType("database").map((n) => failure("database_failure", n, "becomes unreachable")),
    ...ofType("cache").map((n) => failure("redis_failure", n, "restarts with an empty cache")),
    ...ofType("queue").map((n) => failure("kafka_failure", n, "stops accepting writes")),
    ...(entry
      ? [
          {
            id: `scn_traffic_spike_${entry.id}`,
            kind: "traffic_spike" as const,
            label: "Traffic spike",
            description: `Traffic rises sharply at ${entry.name}, following the chosen traffic level.`,
            targetNodeIds: [entry.id],
          },
        ]
      : []),
  ];
}

interface SimulationProfile {
  impact: SimulationImpact;
  metrics: SimulationResult["metrics"];
  errorRate: SimulationResult["errorRate"];
  cascadingFailure: SimulationResult["cascadingFailure"];
  evidenceIds: string[];
  failure: string;
}

/** Fixture numbers keyed by scenario kind (spec §40 example for PostgreSQL). Not engine output. */
const SIMULATION_PROFILES: Record<SimulationScenarioKind, SimulationProfile> = {
  database_failure: {
    impact: "high",
    metrics: [
      { metric: "P99 latency", before: 320, after: 1800, unit: "ms" },
      { metric: "Orders completed", before: 4100, after: 3590, unit: "/min" },
    ],
    errorRate: { before: 0.002, after: 0.124 },
    cascadingFailure: "potential",
    evidenceIds: ["ev_pg_spof", "ev_sim_pg_failure"],
    failure: "becomes unreachable",
  },
  redis_failure: {
    impact: "medium",
    metrics: [
      { metric: "P99 latency", before: 320, after: 640, unit: "ms" },
      { metric: "Database reads", before: 8200, after: 26_200, unit: "/s" },
    ],
    errorRate: { before: 0.002, after: 0.018 },
    cascadingFailure: "potential",
    evidenceIds: ["ev_pg_reads"],
    failure: "restarts with an empty cache",
  },
  kafka_failure: {
    impact: "medium",
    metrics: [
      { metric: "Dispatch delay", before: 2, after: 900, unit: "s" },
      { metric: "P99 latency", before: 320, after: 340, unit: "ms" },
    ],
    errorRate: { before: 0.002, after: 0.004 },
    cascadingFailure: "none",
    evidenceIds: [],
    failure: "stops accepting writes",
  },
  traffic_spike: {
    impact: "high",
    metrics: [
      { metric: "P99 latency", before: 320, after: 950, unit: "ms" },
      { metric: "PostgreSQL connections", before: 410, after: 500, unit: "connections" },
    ],
    errorRate: { before: 0.002, after: 0.031 },
    cascadingFailure: "potential",
    evidenceIds: ["ev_pg_bottleneck"],
    failure: "receives a traffic spike",
  },
  region_failure: {
    impact: "critical",
    metrics: [{ metric: "Requests served", before: 31_000, after: 0, unit: "/s" }],
    errorRate: { before: 0.002, after: 1 },
    cascadingFailure: "likely",
    evidenceIds: [],
    failure: "go down with the region",
  },
  network_partition: {
    impact: "high",
    metrics: [{ metric: "Checkout P99 latency", before: 320, after: 30_000, unit: "ms" }],
    errorRate: { before: 0.002, after: 0.087 },
    cascadingFailure: "likely",
    evidenceIds: ["ev_api_payment_timeout"],
    failure: "becomes unreachable across the partition",
  },
};

const IMPACT_ORDER: readonly SimulationImpact[] = ["low", "medium", "high", "critical"];
const CASCADE_ORDER: readonly SimulationResult["cascadingFailure"][] = ["none", "potential", "likely"];

function escalate<T>(order: readonly T[], value: T): T {
  return order[Math.min(order.indexOf(value) + 1, order.length - 1)] ?? value;
}

/** Mock simulation: affected nodes come from a hard-dependency walk; numbers from SIMULATION_PROFILES. */
function mockSimulationResult(
  architecture: Architecture,
  scenario: SimulationScenario,
  config: SimulationConfig,
): SimulationResult {
  const profile = SIMULATION_PROFILES[scenario.kind];
  const byId = nodeMap(architecture);
  const targets = scenario.targetNodeIds.filter((id) => byId.has(id));
  const spike = scenario.kind === "traffic_spike";
  const levels = hardWalk(architecture, targets, spike ? "downstream" : "upstream");
  const hard = new Set(levels.flat());
  const soft = spike
    ? []
    : [
        ...new Set(
          architecture.edges
            .filter(
              (e) =>
                targets.includes(e.target) && !hard.has(e.source) && byId.get(e.source)?.type !== "client",
            )
            .map((e) => e.source),
        ),
      ];

  const heavy = config.traffic === "10x";
  const impact = heavy ? escalate(IMPACT_ORDER, profile.impact) : profile.impact;
  const cascadingFailure = heavy
    ? escalate(CASCADE_ORDER, profile.cascadingFailure)
    : profile.cascadingFailure;

  const scale = (config.durationMinutes * 60) / 300;
  const at = (seconds: number) => Math.round(seconds * scale);
  const [first = [], second = [], ...rest] = levels;
  const deeper = rest.flat();
  const last = levels.length > 1 ? (levels.at(-1) ?? []) : [];
  const timeline: SimulationTimelineEvent[] = [];
  const push = (
    atSeconds: number,
    phase: SimulationTimelineEvent["phase"],
    nodeIds: string[],
    description: string,
  ) => {
    if (nodeIds.length > 0) timeline.push({ atSeconds: at(atSeconds), phase, nodeIds, description });
  };

  if (spike) {
    push(0, "load_increase", first, `Traffic rises (${config.traffic}) at ${names(architecture, first)}.`);
    push(30, "resource_pressure", second, `CPU and connection pools fill on ${names(architecture, second)}.`);
    push(90, "latency", deeper.length > 0 ? deeper : second, "Latency rises as downstream queues grow.");
  } else {
    push(0, "failure", first, `${names(architecture, first)} ${profile.failure}.`);
    push(
      15,
      "dependency",
      second,
      `${names(architecture, second)} lose their dependency on ${names(architecture, first)}.`,
    );
    push(20, "latency", soft, `${names(architecture, soft)} fall back to slower paths.`);
    push(30, "load_increase", [...second, ...soft], "Retries and fallbacks increase load.");
    push(
      60,
      "resource_pressure",
      second,
      `Worker and connection pools fill on ${names(architecture, second)}.`,
    );
    push(120, "latency", deeper, `Latency rises for ${names(architecture, deeper)}.`);
  }
  if (cascadingFailure !== "none") {
    push(180, "potential_failure", last, `${names(architecture, last)} may start failing requests.`);
  }

  return {
    impact,
    affectedNodeIds: [...hard, ...soft],
    metrics: profile.metrics,
    errorRate: profile.errorRate,
    cascadingFailure,
    timeline: timeline.sort((a, b) => a.atSeconds - b.atSeconds),
    evidenceIds: profile.evidenceIds,
  };
}

const SIMULATION_STEPS = [
  "Preparing environment",
  "Injecting scenario",
  "Propagating dependencies",
  "Measuring impact",
  "Summarizing results",
] as const;
const SIMULATION_STEP_MS = 600;

function simulationView(db: MockDbState, simulation: MockSimulationRecord): SimulationRun {
  const stepIndex = Math.floor((Date.now() - simulation.startedAt) / SIMULATION_STEP_MS);
  let error: SimulationRun["error"] = null;
  if (stepIndex >= SIMULATION_STEPS.length && simulation.result === null) {
    const record = findProject(db, simulation.projectId);
    const architecture = record.versions.find((v) => v.version === simulation.architectureVersion);
    const scenario = scenariosFor(record, architecture ?? null).find(
      (s) => s.id === simulation.config.scenarioId,
    );
    if (architecture && scenario) {
      simulation.result = mockSimulationResult(architecture, scenario, simulation.config);
    } else {
      error = {
        code: "scenario_unavailable",
        message: "The scenario no longer applies to this architecture.",
      };
    }
  }
  const done = simulation.result !== null || error !== null;
  return {
    id: simulation.id,
    projectId: simulation.projectId,
    architectureVersion: simulation.architectureVersion,
    scenarioId: simulation.config.scenarioId,
    config: simulation.config,
    status: error ? "failed" : done ? "succeeded" : "running",
    steps: SIMULATION_STEPS.map((label, i) => ({
      id: `step_${i + 1}`,
      label,
      status:
        done || i < stepIndex
          ? error && i === SIMULATION_STEPS.length - 1
            ? "failed"
            : "done"
          : i === stepIndex
            ? "running"
            : "pending",
    })),
    result: simulation.result,
    error,
  };
}

// --- Comparison (mock diff) -------------------------------------------------

function flatten(value: unknown): ComparisonValue {
  if (value === undefined || value === null) return null;
  if (typeof value === "string" || typeof value === "number" || typeof value === "boolean") return value;
  return JSON.stringify(value);
}

function diffFields(before: Record<string, unknown>, after: Record<string, unknown>): FieldChange[] {
  const keys = [...new Set([...Object.keys(before), ...Object.keys(after)])];
  return keys
    .map((field) => ({ field, before: flatten(before[field]), after: flatten(after[field]) }))
    .filter((c) => c.before !== c.after);
}

function nodeFields(node: ArchitectureNode): Record<string, unknown> {
  return { name: node.name, type: node.type, technology: node.technology, ...node.configuration };
}

interface ComparisonSide {
  label: string;
  version: number | null;
  architecture: Architecture;
  maxDailyActiveUsers: number | null;
  monthlyCost: number | null;
}

function compareArchitectures(
  from: ComparisonSide,
  to: ComparisonSide,
  currency: string,
): ArchitectureComparison {
  const before = nodeMap(from.architecture);
  const after = nodeMap(to.architecture);
  const components: ComponentChange[] = [];
  for (const node of to.architecture.nodes) {
    const previous = before.get(node.id);
    if (!previous) {
      components.push({ change: "added", nodeId: node.id, name: node.name, type: node.type, details: [] });
      continue;
    }
    const details = diffFields(nodeFields(previous), nodeFields(node));
    if (details.length > 0) {
      components.push({ change: "changed", nodeId: node.id, name: node.name, type: node.type, details });
    }
  }
  for (const node of from.architecture.nodes) {
    if (!after.has(node.id)) {
      components.push({ change: "removed", nodeId: node.id, name: node.name, type: node.type, details: [] });
    }
  }

  const key = (e: { source: string; target: string }) => `${e.source}->${e.target}`;
  const edgeFields = (e: Architecture["edges"][number]) => ({
    protocol: e.protocol,
    label: e.label,
    synchronous: e.synchronous,
    critical: e.critical,
  });
  const beforeEdges = new Map(from.architecture.edges.map((e) => [key(e), e]));
  const afterEdges = new Map(to.architecture.edges.map((e) => [key(e), e]));
  const nameIn = (map: Map<string, ArchitectureNode>, id: string) => map.get(id)?.name ?? id;
  const connections: ConnectionChange[] = [];
  for (const [k, edge] of afterEdges) {
    const previous = beforeEdges.get(k);
    const base = {
      edgeId: edge.id,
      sourceName: nameIn(after, edge.source),
      targetName: nameIn(after, edge.target),
    };
    if (!previous) connections.push({ change: "added", ...base, details: [] });
    else {
      const details = diffFields(edgeFields(previous), edgeFields(edge));
      if (details.length > 0) connections.push({ change: "changed", ...base, details });
    }
  }
  for (const [k, edge] of beforeEdges) {
    if (!afterEdges.has(k)) {
      connections.push({
        change: "removed",
        edgeId: edge.id,
        sourceName: nameIn(before, edge.source),
        targetName: nameIn(before, edge.target),
        details: [],
      });
    }
  }
  const order = { added: 0, removed: 1, changed: 2 } as const;
  return {
    from: { label: from.label, version: from.version },
    to: { label: to.label, version: to.version },
    components: components.sort((a, b) => order[a.change] - order[b.change] || a.name.localeCompare(b.name)),
    connections: connections.sort((a, b) => order[a.change] - order[b.change]),
    capacity: {
      beforeMaxDailyActiveUsers: from.maxDailyActiveUsers,
      afterMaxDailyActiveUsers: to.maxDailyActiveUsers,
    },
    cost: { before: from.monthlyCost, after: to.monthlyCost, currency },
  };
}

function versionSide(record: MockProjectRecord, raw: string | null): ComparisonSide {
  const version = Number(raw);
  if (raw === null || !Number.isInteger(version)) {
    throw new MockHttpError(422, "invalid_query", "Pass integer `from` and `to` versions.");
  }
  const architecture = record.versions.find((v) => v.version === version);
  if (!architecture) throw new MockHttpError(404, "version_not_found", `Version ${version} does not exist.`);
  const metrics = record.versionMetrics[String(version)];
  return {
    label: `v${version}`,
    version,
    architecture,
    maxDailyActiveUsers: metrics?.maxDailyActiveUsers ?? null,
    monthlyCost: metrics?.monthlyCost ?? null,
  };
}

function stageSide(record: MockProjectRecord, stageId: string | null): ComparisonSide {
  const stage: EvolutionStage | undefined = record.evolution?.stages.find((s) => s.id === stageId);
  if (!stage) throw new MockHttpError(404, "stage_not_found", `Stage "${stageId}" does not exist.`);
  const architecture =
    stage.architectureVersion !== null
      ? record.versions.find((v) => v.version === stage.architectureVersion)
      : record.stageArchitectures[stage.id];
  if (!architecture) {
    throw new MockHttpError(
      404,
      "stage_architecture_not_found",
      `Stage ${stage.label} has no architecture yet.`,
    );
  }
  return {
    label: stage.label,
    version: stage.architectureVersion,
    architecture,
    maxDailyActiveUsers: stage.maxSupportedDailyActiveUsers,
    monthlyCost: stage.monthlyCost,
  };
}

// --- Discovery (mock) -------------------------------------------------------

const DISCOVERY_STEPS = ["Connect", "Discover", "Normalize", "Generate architecture", "Review"] as const;
const DISCOVERY_STEP_MS = 700;
const DISCOVERED_AFTER_STEP = 2;
const GENERATED_AFTER_STEP = 4;

function discoveryView(db: MockDbState, discovery: MockDiscoveryRecord): DiscoveryRun {
  const stepIndex = Math.floor((Date.now() - discovery.startedAt) / DISCOVERY_STEP_MS);
  const snapshot = db.discoverySnapshots[discovery.connector];
  const record = findProject(db, discovery.projectId);
  const done = stepIndex >= DISCOVERY_STEPS.length;
  const resources = stepIndex >= DISCOVERED_AFTER_STEP ? (snapshot?.resources ?? []) : [];
  const count = (status: string) => resources.filter((r) => r.status === status).length;
  const proposed = stepIndex >= GENERATED_AFTER_STEP ? (snapshot?.architecture ?? null) : null;
  return {
    id: discovery.id,
    projectId: discovery.projectId,
    connector: discovery.connector,
    status: done ? "succeeded" : "running",
    steps: DISCOVERY_STEPS.map((label, i) => ({
      id: `step_${i + 1}`,
      label,
      status: done || i < stepIndex ? "done" : i === stepIndex ? "running" : "pending",
    })),
    resources,
    summary: {
      total: resources.length,
      mapped: count("mapped"),
      unmapped: count("unmapped"),
      ignored: count("ignored"),
    },
    proposedArchitecture: proposed && {
      ...proposed,
      id: `arch_${record.id}`,
      projectId: record.id,
      version: discovery.savedVersion ?? (currentArchitecture(record)?.version ?? 0) + 1,
      createdAt: new Date(discovery.startedAt + GENERATED_AFTER_STEP * DISCOVERY_STEP_MS).toISOString(),
      createdBy: "discovery",
    },
    error: null,
  };
}

function findDiscovery(db: MockDbState, runId: string | undefined): MockDiscoveryRecord {
  const discovery = runId ? db.discoveries[runId] : undefined;
  if (!discovery) throw new MockHttpError(404, "discovery_not_found", `Discovery "${runId}" does not exist.`);
  return discovery;
}

// --- Evidence list ----------------------------------------------------------

function citedEvidenceIds(db: MockDbState, record: MockProjectRecord): Set<string> {
  const ids = new Set<string>();
  const add = (id: string | null | undefined) => {
    if (id) ids.add(id);
  };
  record.capacity?.utilization.forEach((u) => add(u.evidenceId));
  add(record.capacity?.bottleneck?.evidenceId);
  record.validation?.findings.forEach((f) => f.evidenceIds.forEach(add));
  record.reliability?.singlePointsOfFailure.forEach((s) => add(s.evidenceId));
  if (record.reliability?.availability.target != null) add("ev_availability_estimate");
  record.security?.threats.forEach((t) => add(t.evidenceId));
  record.cost?.evidenceIds.forEach(add);
  const latest = record.latestSimulationId ? db.simulations[record.latestSimulationId] : undefined;
  latest?.result?.evidenceIds.forEach(add);
  return ids;
}

// --- Routes -----------------------------------------------------------------

function analysisRoutes<T>(
  path: string,
  action: string,
  notFound: { code: string; message: string },
  get: (record: MockProjectRecord) => T | null,
  run: (record: MockProjectRecord, architecture: Architecture) => T,
): Route[] {
  return [
    route("GET", `/projects/:projectId/${path}`, (p, _b, db) => {
      const result = get(findProject(db, p.projectId));
      if (!result) throw new MockHttpError(404, notFound.code, notFound.message);
      return ok(result);
    }),
    route("POST", `/projects/:projectId/${path}/${action}`, (p, _b, db) => {
      const record = findProject(db, p.projectId);
      return ok(run(record, requireArchitecture(record)));
    }),
  ];
}

const NOT_ANALYZED = { code: "not_analyzed", message: "The current architecture has not been analyzed." };

export const extendedRoutes: Route[] = [
  ...analysisRoutes(
    "reliability",
    "analyze",
    NOT_ANALYZED,
    (r) => r.reliability,
    (record, architecture) => {
      record.reliability = record.baselineReliability = mockReliability(record, architecture);
      return record.reliability;
    },
  ),
  ...analysisRoutes(
    "security",
    "analyze",
    NOT_ANALYZED,
    (r) => r.security,
    (record, architecture) => {
      record.security = record.baselineSecurity = mockSecurity(record, architecture);
      return record.security;
    },
  ),
  ...analysisRoutes(
    "observability",
    "analyze",
    NOT_ANALYZED,
    (r) => r.observability,
    (record, architecture) => {
      record.observability = record.baselineObservability = mockObservability(record, architecture);
      return record.observability;
    },
  ),
  ...analysisRoutes(
    "cost",
    "calculate",
    { code: "not_calculated", message: "The current architecture has no cost estimate yet." },
    (r) => r.cost,
    (record, architecture) => {
      record.cost = record.baselineCost = mockCost(record, architecture);
      return record.cost;
    },
  ),
  ...analysisRoutes(
    "drift",
    "check",
    { code: "not_checked", message: "Drift has not been checked for the current architecture." },
    (r) => r.drift,
    (record, architecture) => {
      record.drift = record.baselineDrift = mockDrift(record, architecture);
      return record.drift;
    },
  ),

  route("GET", "/projects/:projectId/simulation/scenarios", (p, _b, db) => {
    const record = findProject(db, p.projectId);
    return ok(scenariosFor(record, currentArchitecture(record)));
  }),
  route("POST", "/projects/:projectId/simulations", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const config = parseBody(SimulationConfigSchema, body);
    const architecture = requireArchitecture(record);
    if (!scenariosFor(record, architecture).some((s) => s.id === config.scenarioId)) {
      throw new MockHttpError(422, "scenario_not_found", `Scenario "${config.scenarioId}" does not exist.`);
    }
    const simulation: MockSimulationRecord = {
      id: createId("sim"),
      projectId: record.id,
      architectureVersion: architecture.version,
      config,
      startedAt: Date.now(),
      result: null,
    };
    db.simulations[simulation.id] = simulation;
    record.latestSimulationId = simulation.id;
    return ok(simulationView(db, simulation), 202);
  }),
  route("GET", "/projects/:projectId/simulations/latest", (p, _b, db) => {
    const record = findProject(db, p.projectId);
    const simulation = record.latestSimulationId ? db.simulations[record.latestSimulationId] : undefined;
    if (!simulation) {
      throw new MockHttpError(
        404,
        "simulation_not_found",
        "The current architecture has not been simulated.",
      );
    }
    return ok(simulationView(db, simulation));
  }),
  route("GET", "/simulations/:runId", (p, _b, db) => {
    const simulation = p.runId ? db.simulations[p.runId] : undefined;
    if (!simulation)
      throw new MockHttpError(404, "simulation_not_found", `Simulation "${p.runId}" does not exist.`);
    return ok(simulationView(db, simulation));
  }),

  route("GET", "/projects/:projectId/architecture/compare", (p, _b, db, query) => {
    const record = findProject(db, p.projectId);
    const currency = record.cost?.currency ?? record.baselineCost?.currency ?? "USD";
    return ok(
      compareArchitectures(
        versionSide(record, query.get("from")),
        versionSide(record, query.get("to")),
        currency,
      ),
    );
  }),
  route("GET", "/projects/:projectId/evolution", (p, _b, db) => {
    const evolution = findProject(db, p.projectId).evolution;
    if (!evolution)
      throw new MockHttpError(404, "evolution_not_found", "This project has no evolution roadmap yet.");
    return ok(evolution);
  }),
  route("GET", "/projects/:projectId/evolution/compare", (p, _b, db, query) => {
    const record = findProject(db, p.projectId);
    const currency = record.cost?.currency ?? record.baselineCost?.currency ?? "USD";
    return ok(
      compareArchitectures(
        stageSide(record, query.get("from")),
        stageSide(record, query.get("to")),
        currency,
      ),
    );
  }),
  route("GET", "/projects/:projectId/migrations", (p, _b, db) => ok(findProject(db, p.projectId).migrations)),
  route("GET", "/migrations/:migrationId", (p, _b, db) => {
    const migration = db.projects.flatMap((r) => r.migrations).find((m) => m.id === p.migrationId);
    if (!migration) {
      throw new MockHttpError(404, "migration_not_found", `Migration "${p.migrationId}" does not exist.`);
    }
    return ok(migration);
  }),

  route("GET", "/discovery/connectors", (_p, _b, db) => ok(db.connectors)),
  route("POST", "/projects/:projectId/discoveries", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const { connector } = parseBody(DiscoveryRequestSchema, body);
    const connected = db.connectors.find((c) => c.kind === connector)?.status === "connected";
    if (!connected || !db.discoverySnapshots[connector]) {
      throw new MockHttpError(
        422,
        "connector_not_connected",
        `Connect ${connector} before running discovery.`,
      );
    }
    const discovery: MockDiscoveryRecord = {
      id: createId("disc"),
      projectId: record.id,
      connector,
      startedAt: Date.now(),
      savedVersion: null,
    };
    db.discoveries[discovery.id] = discovery;
    return ok(discoveryView(db, discovery), 202);
  }),
  route("GET", "/discoveries/:runId", (p, _b, db) => ok(discoveryView(db, findDiscovery(db, p.runId)))),
  route("POST", "/discoveries/:runId/save", (p, body, db) => {
    const discovery = findDiscovery(db, p.runId);
    const { baseVersion } = parseBody(DiscoverySaveRequestSchema, body);
    const run = discoveryView(db, discovery);
    if (run.status !== "succeeded" || !run.proposedArchitecture) {
      throw new MockHttpError(409, "discovery_not_ready", "Wait for discovery to finish before saving.");
    }
    if (discovery.savedVersion !== null) {
      throw new MockHttpError(409, "discovery_already_saved", `Already saved as v${discovery.savedVersion}.`);
    }
    const record = findProject(db, discovery.projectId);
    const latest = currentArchitecture(record)?.version ?? null;
    if (baseVersion !== latest) {
      throw new MockHttpError(409, "version_conflict", "The architecture changed since discovery started.", {
        latestVersion: latest,
      });
    }
    const label = db.connectors.find((c) => c.kind === discovery.connector)?.label ?? discovery.connector;
    const architecture = commitVersion(
      record,
      run.proposedArchitecture,
      "discovery",
      `Imported from ${label} discovery (${run.summary.mapped} mapped resources)`,
    );
    discovery.savedVersion = architecture.version;
    return ok(architecture);
  }),

  route("GET", "/projects/:projectId/evidence", (p, _b, db) => {
    const ids = citedEvidenceIds(db, findProject(db, p.projectId));
    return ok([...ids].flatMap((id): Evidence[] => (db.evidence[id] ? [db.evidence[id]] : [])));
  }),
];
