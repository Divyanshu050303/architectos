/**
 * MOCK BACKEND ROUTES — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Implements the proposed REST contract (see api/*.ts headers) over the in-memory
 * mock db. Everything computed here — generated architectures, capacity, findings,
 * health scores and AI proposals — is keyword/template-driven fixture data, NOT real
 * analysis. The real numbers must come from the backend engines. Reliability, security,
 * observability, cost, simulation, evolution, discovery and drift routes live in
 * analysis-handlers.ts.
 */
import { z } from "zod";

import { type ArchitectureCommand, CommandError } from "@/features/architecture/types";
import { applyCommands, isSemanticCommand } from "@/features/architecture/utils/commands";
import { formatCompact } from "@/lib/formatting";
import { createId } from "@/lib/utils";
import {
  ArchitectureEdgeSchema,
  ArchitectureNodeSchema,
  LayoutUpdateSchema,
  PositionSchema,
} from "@/schemas/architecture";
import {
  ForgotPasswordInputSchema,
  ResetPasswordInputSchema,
  SignInInputSchema,
  SignUpInputSchema,
} from "@/schemas/auth";
import { DecisionInputSchema } from "@/schemas/decisions";
import { ProjectInputSchema } from "@/schemas/projects";
import { ProposalRequestSchema } from "@/schemas/proposals";
import { NonFunctionalRequirementsSchema } from "@/schemas/requirements";
import { SEVERITIES } from "@/schemas/validation";
import type {
  Architecture,
  ArchitectureEdge,
  ArchitectureNode,
  Proposal,
  ProposalChange,
} from "@/types/architecture";
import type { CapacityAnalysis, ComponentUtilization } from "@/types/capacity";
import type { Job, Project } from "@/types/project";
import type { Finding, HealthCategoryScore, Severity, ValidationReport } from "@/types/validation";

import { extendedRoutes } from "./analysis-handlers";
import { mockPasswordSignIn, mockResetPassword, mockSession, mockSignOut, mockSignUp } from "./auth";
import { getMockDb, saveMockDb } from "./db";
import {
  type MockDbState,
  type MockJobRecord,
  type MockProjectRecord,
  mockEdge,
  mockNode,
  newProjectRecord,
} from "./fixtures";
import {
  assertBaseVersion,
  commitVersion,
  currentArchitecture,
  findProject,
  MockHttpError,
  type MockRequest,
  type MockResponse,
  noContent,
  type Params,
  now,
  ok,
  parseBody,
  rememberVersionMetrics,
  replicasOf,
  requireArchitecture,
  type Route,
  route,
  uniqueId,
} from "./router";

export { MockHttpError, type MockRequest, type MockResponse } from "./router";

const SEVERITY_RANK: Record<Severity, number> = Object.fromEntries(
  SEVERITIES.map((s, i) => [s, i]),
) as Record<Severity, number>;

/** Mock of the backend's project summary. */
function toProject(record: MockProjectRecord): Project {
  const open = (record.validation?.findings ?? [])
    .filter((f) => f.status === "open")
    .sort((a, b) => SEVERITY_RANK[a.severity] - SEVERITY_RANK[b.severity]);
  const status: Project["summary"]["status"] = !record.validation
    ? "unknown"
    : open.some((f) => f.severity === "critical")
      ? "critical"
      : open.some((f) => f.severity === "high" || f.severity === "medium")
        ? "warning"
        : "healthy";
  const utilizations = record.capacity?.utilization.map((u) => u.utilization) ?? [];
  return {
    id: record.id,
    name: record.name,
    description: record.description,
    createdAt: record.createdAt,
    updatedAt: record.updatedAt,
    architectureVersion: currentArchitecture(record)?.version ?? null,
    summary: {
      status,
      dailyActiveUsers: record.requirements.nonFunctional.dailyActiveUsers,
      peakRps: record.requirements.nonFunctional.peakRps,
      capacityUtilization: utilizations.length > 0 ? Math.max(...utilizations) : null,
      topIssue: open[0]?.title ?? null,
    },
  };
}

// --- Commands ---------------------------------------------------------------

const CommandSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("ADD_COMPONENT"), node: ArchitectureNodeSchema }),
  z.object({ type: z.literal("REMOVE_COMPONENTS"), nodeIds: z.array(z.string()) }),
  z.object({ type: z.literal("CONNECT_COMPONENTS"), edge: ArchitectureEdgeSchema }),
  z.object({ type: z.literal("REMOVE_CONNECTIONS"), edgeIds: z.array(z.string()) }),
  z.object({ type: z.literal("RENAME_COMPONENT"), nodeId: z.string(), name: z.string() }),
  z.object({
    type: z.literal("UPDATE_CONFIGURATION"),
    nodeId: z.string(),
    configuration: z.record(z.string(), z.unknown()),
  }),
  z.object({ type: z.literal("CHANGE_REPLICAS"), nodeId: z.string(), replicas: z.number() }),
  z.object({ type: z.literal("MOVE_COMPONENTS"), positions: z.record(z.string(), PositionSchema) }),
]) satisfies z.ZodType<ArchitectureCommand>;

const SaveCommandsBodySchema = z.object({
  baseVersion: z.number().int(),
  commands: z.array(CommandSchema).min(1),
});

function applyOrReject(architecture: Architecture, commands: readonly ArchitectureCommand[]): Architecture {
  try {
    return applyCommands(architecture, commands);
  } catch (error) {
    if (error instanceof CommandError) throw new MockHttpError(422, "invalid_command", error.message);
    throw error;
  }
}

function describeCommand(command: ArchitectureCommand, architecture: Architecture): string {
  const name = (id: string) => architecture.nodes.find((n) => n.id === id)?.name ?? id;
  switch (command.type) {
    case "ADD_COMPONENT":
      return `Added ${command.node.name}`;
    case "REMOVE_COMPONENTS":
      return `Removed ${command.nodeIds.map(name).join(", ")}`;
    case "CONNECT_COMPONENTS":
      return `Connected ${name(command.edge.source)} → ${name(command.edge.target)}`;
    case "REMOVE_CONNECTIONS":
      return `Removed ${command.edgeIds.length} connection(s)`;
    case "RENAME_COMPONENT":
      return `Renamed ${name(command.nodeId)} to ${command.name}`;
    case "UPDATE_CONFIGURATION":
      return `Updated ${name(command.nodeId)} configuration`;
    case "CHANGE_REPLICAS":
      return `Set ${name(command.nodeId)} replicas to ${command.replicas}`;
    case "MOVE_COMPONENTS":
      return "Moved components";
  }
}

function summarizeCommands(commands: readonly ArchitectureCommand[], architecture: Architecture): string {
  const parts = commands.filter(isSemanticCommand).map((c) => describeCommand(c, architecture));
  const shown = parts.slice(0, 3).join("; ");
  return parts.length > 3 ? `${shown}; +${parts.length - 3} more` : shown;
}

/** Proposal diffs become ordinary commands when applied (spec §89). */
function changesToCommands(changes: readonly ProposalChange[]): ArchitectureCommand[] {
  return changes.map((change): ArchitectureCommand => {
    switch (change.op) {
      case "add_node":
        return { type: "ADD_COMPONENT", node: change.node };
      case "remove_node":
        return { type: "REMOVE_COMPONENTS", nodeIds: [change.nodeId] };
      case "add_edge":
        return { type: "CONNECT_COMPONENTS", edge: change.edge };
      case "remove_edge":
        return { type: "REMOVE_CONNECTIONS", edgeIds: [change.edgeId] };
      case "update_node":
        if (change.field === "name") {
          return { type: "RENAME_COMPONENT", nodeId: change.nodeId, name: String(change.after) };
        }
        if (change.field === "replicas") {
          return { type: "CHANGE_REPLICAS", nodeId: change.nodeId, replicas: Number(change.after) };
        }
        return {
          type: "UPDATE_CONFIGURATION",
          nodeId: change.nodeId,
          configuration: { [change.field]: change.after },
        };
    }
  });
}

// --- Mock generation (template, keyword-driven) -----------------------------

const JOB_STEPS = [
  "Analyzing requirements",
  "Checking assumptions",
  "Selecting components",
  "Building architecture",
  "Calculating capacity",
  "Running validation",
] as const;
const JOB_STEP_MS = 700;

function templateArchitecture(record: MockProjectRecord): Architecture {
  const { requirements } = record;
  const text = `${requirements.description} ${requirements.functional.join(" ")}`.toLowerCase();
  const nodes: ArchitectureNode[] = [
    mockNode("client", "client", "Clients", "Web / Mobile", { x: 300, y: 0 }),
    mockNode("lb", "load_balancer", "Load Balancer", "Managed load balancer", { x: 300, y: 150 }),
    mockNode(
      "api",
      "service",
      "API",
      "Service",
      { x: 300, y: 300 },
      { replicas: 2, cpu: "2 vCPU", memory: "4 GB" },
    ),
    mockNode(
      "postgres",
      "database",
      "PostgreSQL",
      "PostgreSQL",
      { x: 300, y: 450 },
      { replicas: 1, maxConnections: 300 },
      { description: "Primary store" },
    ),
  ];
  const edges: ArchitectureEdge[] = [
    mockEdge("e_client_lb", "client", "lb", "HTTPS"),
    mockEdge("e_lb_api", "lb", "api", "HTTP"),
    mockEdge("e_api_postgres", "api", "postgres", "SQL"),
  ];
  if (/cache|read/.test(text)) {
    nodes.push(
      mockNode("redis", "cache", "Redis", "Redis 7", { x: 600, y: 450 }, { replicas: 1, memory: "4 GB" }),
    );
    edges.push(mockEdge("e_api_redis", "api", "redis", "RESP", { critical: false }));
  }
  if (/event|queue|async/.test(text)) {
    nodes.push(
      mockNode("kafka", "queue", "Kafka", "Apache Kafka", { x: 0, y: 450 }, { brokers: 3 }),
      mockNode("worker", "worker", "Worker", "Service", { x: 0, y: 600 }, { replicas: 1 }),
    );
    edges.push(
      mockEdge("e_api_kafka", "api", "kafka", "Kafka", { synchronous: false, critical: false }),
      mockEdge("e_kafka_worker", "kafka", "worker", "Kafka", { synchronous: false, critical: false }),
      mockEdge("e_worker_postgres", "worker", "postgres", "SQL", { critical: false }),
    );
  }
  return {
    id: `arch_${record.id}`,
    projectId: record.id,
    version: 1,
    nodes,
    edges,
    assumptions: [
      {
        id: "A-001",
        statement: "Mock template: built from requirement keywords, not by the AI service.",
        source: "default",
      },
    ],
    createdAt: now(),
    createdBy: "ai",
  };
}

const DEFAULT_ROWS: Partial<
  Record<ArchitectureNode["type"], (node: ArchitectureNode) => ComponentUtilization>
> = {
  service: (n) => row(n.id, "cpu", 45, 100, "%", 0.75),
  worker: (n) => row(n.id, "cpu", 40, 100, "%", 0.75),
  database: (n) => {
    const max = typeof n.configuration.maxConnections === "number" ? n.configuration.maxConnections : 500;
    return row(n.id, "connections", Math.round(max * 0.5), max, "connections", 0.7);
  },
  cache: (n) => row(n.id, "memory", 30, 100, "%", 0.8),
  queue: (n) => row(n.id, "throughput", 20, 100, "MB/s", 0.75),
};

function row(
  nodeId: string,
  resource: string,
  used: number,
  limit: number,
  unit: string,
  threshold: number,
): ComponentUtilization {
  const utilization = used / limit;
  const status = utilization >= 0.95 ? "critical" : utilization >= threshold ? "warning" : "healthy";
  return { nodeId, resource, used, limit, unit, utilization, threshold, status, evidenceId: null };
}

/** Mock capacity: keeps baseline fixture rows for surviving nodes, fills template rows for new ones. */
function mockCapacity(record: MockProjectRecord, architecture: Architecture): CapacityAnalysis {
  const baseline = record.baselineCapacity;
  const nodeIds = new Set(architecture.nodes.map((n) => n.id));
  const edgeIds = new Set(architecture.edges.map((e) => e.id));
  const nf = record.requirements.nonFunctional;
  const dau = baseline?.load.dailyActiveUsers ?? nf.dailyActiveUsers ?? 100_000;
  const peakRps = baseline?.load.peakRps ?? nf.peakRps ?? Math.max(1, Math.round(dau / 100));
  const load = baseline?.load ?? {
    dailyActiveUsers: dau,
    peakRps,
    writesPerSecond: Math.round(peakRps * 0.25),
  };

  const kept = (baseline?.utilization ?? []).filter((u) => nodeIds.has(u.nodeId));
  const covered = new Set(kept.map((u) => u.nodeId));
  const added = architecture.nodes
    .filter((n) => !covered.has(n.id))
    .flatMap((n) => DEFAULT_ROWS[n.type]?.(n) ?? []);
  const utilization = [...kept, ...added];

  const baselineEdges = new Map((baseline?.edges ?? []).map((e) => [e.edgeId, e.rps]));
  const edges = architecture.edges
    .filter((e) => edgeIds.has(e.id))
    .map((e) => ({ edgeId: e.id, rps: baselineEdges.get(e.id) ?? Math.round(peakRps * 0.1) }));

  let bottleneck =
    baseline?.bottleneck && nodeIds.has(baseline.bottleneck.nodeId) ? baseline.bottleneck : null;
  const worst = [...utilization].sort((a, b) => b.utilization / b.threshold - a.utilization / a.threshold)[0];
  if (!bottleneck && worst) {
    const node = architecture.nodes.find((n) => n.id === worst.nodeId);
    bottleneck = {
      nodeId: worst.nodeId,
      resource: worst.resource,
      description: `${node?.name ?? worst.nodeId} ${worst.resource} (mock template estimate).`,
      thresholdDailyActiveUsers: Math.round((dau * worst.threshold) / Math.max(worst.utilization, 0.01)),
      evidenceId: null,
    };
  }
  const maxSupported = bottleneck?.thresholdDailyActiveUsers ?? dau * 4;
  const envelope = baseline?.envelope ?? {
    maxSupportedDailyActiveUsers: maxSupported,
    points: [
      { label: formatCompact(dau / 10), dailyActiveUsers: dau / 10, status: "supported" as const },
      { label: formatCompact(dau), dailyActiveUsers: dau, status: "current" as const },
      { label: formatCompact(maxSupported), dailyActiveUsers: maxSupported, status: "warning" as const },
      {
        label: formatCompact(maxSupported * 2),
        dailyActiveUsers: maxSupported * 2,
        status: "exceeded" as const,
      },
    ],
  };

  return {
    projectId: record.id,
    architectureVersion: architecture.version,
    calculatedAt: now(),
    load,
    utilization,
    edges,
    bottleneck,
    envelope,
  };
}

const SEVERITY_PENALTY: Record<Severity, number> = { critical: 12, high: 6, medium: 3, low: 1, info: 0 };

function spofFinding(node: ArchitectureNode): Finding {
  return {
    id: `f_spof_${node.id}`,
    ruleId: "availability.single_point_of_failure",
    category: "reliability",
    severity: "critical",
    title: `${node.name} is a single point of failure`,
    location: node.name,
    whyItMatters: "One instance failure makes every dependent request fail.",
    recommendation: "Run at least 2 replicas with automatic failover.",
    nodeIds: [node.id],
    edgeIds: [],
    evidenceIds: [],
    fixable: true,
    status: "open",
  };
}

/** Whether a baseline finding still holds on `architecture` (a couple of mock rules). */
function stillHolds(finding: Finding, architecture: Architecture): boolean {
  const nodes = finding.nodeIds.map((id) => architecture.nodes.find((n) => n.id === id));
  if (nodes.some((n) => n === undefined)) return false;
  if (finding.ruleId === "availability.single_point_of_failure") return replicasOf(nodes[0]) < 2;
  if (finding.ruleId === "resilience.missing_timeout") return nodes[0]?.configuration.timeoutMs === undefined;
  return true;
}

/** Mock validation: re-checks baseline findings, adds SPOF findings, adjusts baseline scores. */
function mockValidation(record: MockProjectRecord, architecture: Architecture): ValidationReport {
  const baseline = record.baselineValidation;
  const previous = new Map((record.validation?.findings ?? baseline?.findings ?? []).map((f) => [f.id, f]));
  const kept = (baseline?.findings ?? []).filter((f) => stillHolds(f, architecture));
  const keptIds = new Set(kept.map((f) => f.id));
  const added = architecture.nodes
    .filter((n) => n.type === "database" && replicasOf(n) < 2 && !keptIds.has(`f_spof_${n.id}`))
    .map(spofFinding);
  const findings = [...kept, ...added].map((f) => ({ ...f, status: previous.get(f.id)?.status ?? f.status }));

  const removed = (baseline?.findings ?? []).filter((f) => !keptIds.has(f.id));
  const categories: HealthCategoryScore[] = (
    ["capacity", "reliability", "security", "observability", "cost"] as const
  ).map((category) => {
    const base = baseline?.health.categories.find((c) => c.category === category);
    const inCategory = (list: Finding[]) => list.filter((f) => f.category === category);
    const penalty = (list: Finding[]) =>
      inCategory(list).reduce((sum, f) => sum + SEVERITY_PENALTY[f.severity], 0);
    const score = base ? base.score + penalty(removed) - penalty(added) : 100 - penalty(findings);
    const changed = inCategory(removed).length + inCategory(added).length > 0;
    return {
      category,
      score: Math.min(100, Math.max(0, score)),
      findingIds: inCategory(findings).map((f) => f.id),
      summary:
        base && !changed
          ? base.summary
          : `Mock re-validation: ${inCategory(findings).length} finding(s) in this category.`,
    };
  });
  const average = categories.reduce((sum, c) => sum + c.score, 0) / categories.length;
  const baseAverage = baseline
    ? baseline.health.categories.reduce((sum, c) => sum + c.score, 0) / baseline.health.categories.length
    : average;
  const overall = baseline ? baseline.health.overall + (average - baseAverage) : average;

  return {
    projectId: record.id,
    architectureVersion: architecture.version,
    validatedAt: now(),
    findings,
    health: { overall: Math.round(Math.min(100, Math.max(0, overall))), categories },
  };
}

function jobView(db: MockDbState, job: MockJobRecord): Job {
  const stepIndex = Math.floor((Date.now() - job.startedAt) / JOB_STEP_MS);
  if (stepIndex >= JOB_STEPS.length && job.resultVersion === null) {
    const record = findProject(db, job.projectId);
    const architecture = commitVersion(
      record,
      templateArchitecture(record),
      "ai",
      "Generated from requirements (mock template)",
    );
    record.capacity = record.baselineCapacity = mockCapacity(
      { ...record, baselineCapacity: null },
      architecture,
    );
    record.validation = record.baselineValidation = mockValidation(
      { ...record, baselineValidation: null, validation: null },
      architecture,
    );
    job.resultVersion = architecture.version;
  }
  const done = job.resultVersion !== null;
  return {
    id: job.id,
    kind: "generate_architecture",
    status: done ? "succeeded" : "running",
    steps: JOB_STEPS.map((label, i) => ({
      id: `step_${i + 1}`,
      label,
      status: done || i < stepIndex ? "done" : i === stepIndex ? "running" : "pending",
    })),
    result: done && job.resultVersion !== null ? { architectureVersion: job.resultVersion } : null,
    error: null,
  };
}

// --- Mock AI proposals (keyword rules) --------------------------------------

type ProposalDraft = Omit<Proposal, "id" | "projectId" | "prompt" | "baseVersion" | "status" | "validation">;

const MOCK_NOTE = "Mock mode: figures are illustrative fixtures.";

function answer(recommendation: string, reason: string, evidenceIds: string[] = []): ProposalDraft {
  return { kind: "answer", recommendation, reason, impact: [], cost: null, evidenceIds, changes: [] };
}

function pickService(
  architecture: Architecture,
  selectedIds: readonly string[],
): ArchitectureNode | undefined {
  const services = architecture.nodes.filter((n) => n.type === "service" || n.type === "gateway");
  return (
    services.find((n) => selectedIds.includes(n.id)) ??
    services.find((n) => /^api\b/i.test(n.name)) ??
    services[0]
  );
}

function cacheProposal(architecture: Architecture, selectedIds: readonly string[]): ProposalDraft {
  const source = pickService(architecture, selectedIds);
  if (!source) return answer("Add a service first.", "There is no service that could read through a cache.");
  const ids = new Set(architecture.nodes.map((n) => n.id));
  const id = uniqueId("redis", ids);
  const name = architecture.nodes.some((n) => n.name === "Redis") ? "Redis Read Cache" : "Redis";
  const node = mockNode(
    id,
    "cache",
    name,
    "Redis 7",
    { x: source.position.x + 300, y: source.position.y + 150 },
    { replicas: 1, memory: "8 GB" },
    { description: "Read-through cache" },
  );
  const edge = mockEdge(
    uniqueId(`e_${source.id}_${id}`, new Set(architecture.edges.map((e) => e.id))),
    source.id,
    id,
    "RESP",
    { critical: false },
  );
  return {
    kind: "change",
    recommendation: "Introduce Redis.",
    reason: `PostgreSQL read utilization exceeds the configured threshold. ${MOCK_NOTE}`,
    impact: [
      { metric: "Database reads", before: 18_000, after: 6_000, unit: "/s", evidenceId: "ev_cache_reads" },
    ],
    cost: { before: 1240, after: 1277, currency: "USD", period: "month" },
    evidenceIds: ["ev_cache_reads", "ev_cache_cost"],
    changes: [
      { op: "add_node", node },
      { op: "add_edge", edge, sourceName: source.name, targetName: name },
    ],
  };
}

function queuePair(
  architecture: Architecture,
  selectedIds: readonly string[],
): [ArchitectureNode, ArchitectureNode] | null {
  const byId = (id: string) => architecture.nodes.find((n) => n.id === id);
  const [first, second] = selectedIds.map(byId).filter((n): n is ArchitectureNode => n !== undefined);
  if (first && second) {
    const reversed = architecture.edges.some((e) => e.source === second.id && e.target === first.id);
    return reversed ? [second, first] : [first, second];
  }
  const api = architecture.nodes.find((n) => n.name === "API");
  const order = architecture.nodes.find((n) => n.name === "Order Service");
  if (api && order) return [api, order];
  const isService = (n: ArchitectureNode | undefined) => n?.type === "service";
  const edge = architecture.edges.find(
    (e) => e.synchronous && isService(byId(e.source)) && isService(byId(e.target)),
  );
  const source = edge && byId(edge.source);
  const target = edge && byId(edge.target);
  return source && target ? [source, target] : null;
}

function queueProposal(architecture: Architecture, selectedIds: readonly string[]): ProposalDraft {
  const pair = queuePair(architecture, selectedIds);
  if (!pair) {
    return answer("Select two services to put a queue between.", "No pair of connected services was found.");
  }
  const [source, target] = pair;
  const nodeIds = new Set(architecture.nodes.map((n) => n.id));
  const edgeIds = new Set(architecture.edges.map((e) => e.id));
  const id = uniqueId("queue", nodeIds);
  const name = `${target.name} Queue`;
  const node = mockNode(
    id,
    "queue",
    name,
    "Apache Kafka",
    { x: (source.position.x + target.position.x) / 2 + 160, y: (source.position.y + target.position.y) / 2 },
    { brokers: 3, partitions: 6 },
    { description: `Decouples ${source.name} from ${target.name}` },
  );
  const existing = architecture.edges.find((e) => e.source === source.id && e.target === target.id);
  const changes: ProposalChange[] = [];
  if (existing) {
    changes.push({
      op: "remove_edge",
      edgeId: existing.id,
      sourceName: source.name,
      targetName: target.name,
    });
  }
  changes.push(
    { op: "add_node", node },
    {
      op: "add_edge",
      edge: mockEdge(uniqueId(`e_${source.id}_${id}`, edgeIds), source.id, id, "Kafka", {
        synchronous: false,
        critical: false,
      }),
      sourceName: source.name,
      targetName: name,
    },
    {
      op: "add_edge",
      edge: mockEdge(uniqueId(`e_${id}_${target.id}`, edgeIds), id, target.id, "Kafka", {
        synchronous: false,
        critical: false,
      }),
      sourceName: name,
      targetName: target.name,
    },
  );
  return {
    kind: "change",
    recommendation: `Add a queue between ${source.name} and ${target.name}.`,
    reason: `${source.name} no longer waits for ${target.name}; spikes are buffered in the queue. ${MOCK_NOTE}`,
    impact: [
      {
        metric: "Synchronous hops on this path",
        before: 1,
        after: 0,
        unit: "",
        evidenceId: "ev_queue_decoupling",
      },
    ],
    cost: null,
    evidenceIds: ["ev_queue_decoupling"],
    changes,
  };
}

/**
 * MOCK rule: a reporting database with a single replica. It deliberately trips the
 * fixture single-point-of-failure rule, so the proposal's validation shows a new
 * critical finding (demo of spec §89 validate-before-approve).
 */
function databaseProposal(architecture: Architecture, selectedIds: readonly string[]): ProposalDraft {
  const source = pickService(architecture, selectedIds);
  if (!source) return answer("Add a service first.", "There is no service that could use a database.");
  const id = uniqueId("analytics_db", new Set(architecture.nodes.map((n) => n.id)));
  const name = architecture.nodes.some((n) => n.name === "Analytics DB")
    ? `Analytics DB (${id})`
    : "Analytics DB";
  const node = mockNode(
    id,
    "database",
    name,
    "PostgreSQL 16",
    { x: source.position.x + 300, y: source.position.y - 150 },
    { replicas: 1, storage: "500 GB" },
    { description: "Reporting / analytics database" },
  );
  const edge = mockEdge(
    uniqueId(`e_${source.id}_${id}`, new Set(architecture.edges.map((e) => e.id))),
    source.id,
    id,
    "SQL",
    { critical: false },
  );
  return {
    kind: "change",
    recommendation: "Add a separate analytics database.",
    reason: `Reporting queries move off the primary database. It starts with one replica. ${MOCK_NOTE}`,
    impact: [],
    cost: { before: 1240, after: 1420, currency: "USD", period: "month" },
    evidenceIds: [],
    changes: [
      { op: "add_node", node },
      { op: "add_edge", edge, sourceName: source.name, targetName: name },
    ],
  };
}

function bottleneckAnswer(record: MockProjectRecord, architecture: Architecture): ProposalDraft {
  const bottleneck = (record.capacity ?? record.baselineCapacity)?.bottleneck;
  if (!bottleneck)
    return answer("Run a capacity analysis first.", "No capacity analysis exists for this architecture.");
  const node = architecture.nodes.find((n) => n.id === bottleneck.nodeId);
  return answer(
    `${node?.name ?? bottleneck.nodeId} ${bottleneck.resource} break first, at ~${formatCompact(
      bottleneck.thresholdDailyActiveUsers,
    )} DAU.`,
    `${bottleneck.description} Beyond that point requests start failing before other components saturate.`,
    bottleneck.evidenceId ? [bottleneck.evidenceId] : [],
  );
}

function explainAnswer(architecture: Architecture): ProposalDraft {
  const counts = new Map<string, number>();
  for (const node of architecture.nodes) counts.set(node.type, (counts.get(node.type) ?? 0) + 1);
  const composition = [...counts]
    .map(([type, count]) => `${count} ${type.replaceAll("_", " ")}${count === 1 ? "" : "s"}`)
    .join(", ");
  const targets = new Set(architecture.edges.map((e) => e.target));
  const entryPoints = architecture.nodes
    .filter((n) => n.type === "client" || (!targets.has(n.id) && n.type !== "observability"))
    .map((n) => n.name);
  const critical = architecture.edges.filter((e) => e.critical && e.synchronous).length;
  return answer(
    `This architecture has ${architecture.nodes.length} components and ${architecture.edges.length} connections.`,
    `Components: ${composition}. Entry points: ${entryPoints.join(", ") || "none"}. ` +
      `${critical} connection(s) are critical and synchronous.`,
  );
}

function draftProposal(
  record: MockProjectRecord,
  prompt: string,
  selectedIds: readonly string[],
): ProposalDraft {
  const architecture = requireArchitecture(record);
  const text = prompt.toLowerCase();
  if (/redis|cache/.test(text)) return cacheProposal(architecture, selectedIds);
  if (/queue|kafka|async/.test(text)) return queueProposal(architecture, selectedIds);
  if (/database|analytics db|reporting db/.test(text)) return databaseProposal(architecture, selectedIds);
  if (/10m|scale|break|bottleneck/.test(text)) return bottleneckAnswer(record, architecture);
  if (/explain/.test(text)) return explainAnswer(architecture);
  return answer(
    "Mock mode understands only a few example commands.",
    "Try “Add Redis caching”, “Add a queue between these services”, “Add an analytics database”, “What breaks at 10M users?” or " +
      "“Explain this architecture” — connect the backend AI service for full support.",
  );
}

function fixDraft(architecture: Architecture, finding: Finding): ProposalDraft {
  const node = architecture.nodes.find((n) => n.id === finding.nodeIds[0]);
  if (!finding.fixable || !node) {
    throw new MockHttpError(422, "not_fixable", "This finding has no automatic fix.");
  }
  const base = {
    kind: "change" as const,
    recommendation: finding.recommendation,
    reason: `${finding.whyItMatters} ${MOCK_NOTE}`,
    impact: [],
    cost: null,
    evidenceIds: finding.evidenceIds,
  };
  if (finding.ruleId === "availability.single_point_of_failure") {
    const before = replicasOf(node);
    return {
      ...base,
      changes: [
        { op: "update_node", nodeId: node.id, name: node.name, field: "replicas", before, after: before + 1 },
      ],
    };
  }
  if (finding.ruleId === "resilience.missing_timeout") {
    return {
      ...base,
      changes: [
        {
          op: "update_node",
          nodeId: node.id,
          name: node.name,
          field: "timeoutMs",
          before: null,
          after: 2000,
        },
      ],
    };
  }
  throw new MockHttpError(422, "not_fixable", "Mock mode has no automatic fix for this rule.");
}

const OPEN = (f: Finding) => f.status === "open";

/**
 * MOCK validation of a proposal (spec §89 "Validate" before preview/approval): runs the
 * same fixture rules as mockValidation on the architecture the diff would produce and
 * compares open findings. Fixture logic, not the backend validation engine.
 */
function mockProposalValidation(record: MockProjectRecord, draft: ProposalDraft): Proposal["validation"] {
  if (draft.kind !== "change" || draft.changes.length === 0) return null;
  const current = requireArchitecture(record);
  let next: Architecture;
  try {
    next = applyCommands(current, changesToCommands(draft.changes));
  } catch (error) {
    if (!(error instanceof CommandError)) throw error;
    return {
      summary: `Mock validation: the change cannot be applied (${error.message}).`,
      newFindings: [],
      resolvedFindingIds: [],
      passes: false,
    };
  }
  const before = (record.validation ?? mockValidation(record, current)).findings.filter(OPEN);
  const after = mockValidation(record, next).findings.filter(OPEN);
  const beforeIds = new Set(before.map((f) => f.id));
  const afterIds = new Set(after.map((f) => f.id));
  const newFindings = after
    .filter((f) => !beforeIds.has(f.id))
    .map(({ severity, title, location }) => ({ severity, title, location }));
  const resolvedFindingIds = before.filter((f) => !afterIds.has(f.id)).map((f) => f.id);
  const parts = [
    newFindings.length === 0 ? "No new findings" : `${newFindings.length} new finding(s)`,
    resolvedFindingIds.length > 0 ? `${resolvedFindingIds.length} resolved` : null,
  ].filter(Boolean);
  return {
    summary: `Mock validation (fixture rules): ${parts.join(", ")}.`,
    newFindings,
    resolvedFindingIds,
    passes: newFindings.length === 0,
  };
}

function storeProposal(
  db: MockDbState,
  record: MockProjectRecord,
  prompt: string,
  baseVersion: number,
  draft: ProposalDraft,
): Proposal {
  const proposal: Proposal = {
    ...draft,
    validation: mockProposalValidation(record, draft),
    id: createId("prop"),
    projectId: record.id,
    prompt,
    baseVersion,
    status: "pending",
  };
  db.proposals[proposal.id] = proposal;
  return proposal;
}

function findProposal(db: MockDbState, proposalId: string | undefined): Proposal {
  const proposal = proposalId ? db.proposals[proposalId] : undefined;
  if (!proposal)
    throw new MockHttpError(404, "proposal_not_found", `Proposal "${proposalId}" does not exist.`);
  return proposal;
}

// --- Routes -----------------------------------------------------------------

const RequirementsBodySchema = z.object({
  description: z.string(),
  functional: z.array(z.string()),
  nonFunctional: NonFunctionalRequirementsSchema,
});
const BaseVersionSchema = z.object({ baseVersion: z.number().int() });
const LayoutBodySchema = LayoutUpdateSchema.extend({ baseVersion: z.number().int() });
const FindingStatusSchema = z.object({ status: z.enum(["open", "ignored"]) });

const routes: Route[] = [
  route("GET", "/auth/session", () => {
    const user = mockSession();
    if (!user) throw new MockHttpError(401, "unauthenticated", "Sign in to continue.");
    return ok({ user });
  }),
  route("POST", "/auth/login", (_p, body) => {
    const input = parseBody(SignInInputSchema, body);
    const user = mockPasswordSignIn(input.email, input.password);
    if (!user) throw new MockHttpError(401, "invalid_credentials", "Email or password is incorrect.");
    return ok({ user });
  }),
  route("POST", "/auth/signup", (_p, body) => {
    const input = parseBody(SignUpInputSchema, body);
    const user = mockSignUp(input.name, input.email, input.password);
    if (!user) throw new MockHttpError(409, "email_taken", "An account with this email already exists.");
    return ok({ user }, 201);
  }),
  route("POST", "/auth/password/forgot", (_p, body) => {
    parseBody(ForgotPasswordInputSchema, body);
    return noContent();
  }),
  route("POST", "/auth/password/reset", (_p, body) => {
    const input = parseBody(ResetPasswordInputSchema, body);
    if (!mockResetPassword(input.token, input.password)) {
      throw new MockHttpError(400, "invalid_token", "This reset link has expired or was already used.");
    }
    return noContent();
  }),
  route("POST", "/auth/logout", () => {
    mockSignOut();
    return noContent();
  }),
  route("GET", "/projects", (_p, _b, db) =>
    ok([...db.projects].sort((a, b) => b.updatedAt.localeCompare(a.updatedAt)).map(toProject)),
  ),
  route("POST", "/projects", (_p, body, db) => {
    const input = parseBody(ProjectInputSchema, body);
    const record = newProjectRecord(createId("proj"), input.name, input.description, now());
    db.projects.push(record);
    return ok(toProject(record), 201);
  }),
  route("GET", "/projects/:projectId", (p, _b, db) => ok(toProject(findProject(db, p.projectId)))),
  route("PATCH", "/projects/:projectId", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const input = parseBody(ProjectInputSchema.partial(), body);
    if (input.name !== undefined) record.name = input.name;
    if (input.description !== undefined) record.description = input.description;
    record.updatedAt = now();
    return ok(toProject(record));
  }),

  route("GET", "/projects/:projectId/requirements", (p, _b, db) =>
    ok(findProject(db, p.projectId).requirements),
  ),
  route("PUT", "/projects/:projectId/requirements", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const input = parseBody(RequirementsBodySchema, body);
    record.requirements = { ...input, projectId: record.id, updatedAt: now() };
    record.updatedAt = now();
    return ok(record.requirements);
  }),

  route("POST", "/projects/:projectId/architecture/generate", (p, _b, db) => {
    const record = findProject(db, p.projectId);
    if (!record.requirements.updatedAt || !record.requirements.description.trim()) {
      throw new MockHttpError(
        422,
        "requirements_missing",
        "Save the requirements before generating an architecture.",
      );
    }
    const job: MockJobRecord = {
      id: createId("job"),
      projectId: record.id,
      startedAt: Date.now(),
      resultVersion: null,
    };
    db.jobs[job.id] = job;
    return ok(jobView(db, job), 202);
  }),
  route("GET", "/jobs/:jobId", (p, _b, db) => {
    const job = p.jobId ? db.jobs[p.jobId] : undefined;
    if (!job) throw new MockHttpError(404, "job_not_found", `Job "${p.jobId}" does not exist.`);
    return ok(jobView(db, job));
  }),

  route("GET", "/projects/:projectId/architecture", (p, _b, db) => {
    const architecture = currentArchitecture(findProject(db, p.projectId));
    if (!architecture)
      throw new MockHttpError(404, "architecture_not_found", "This project has no architecture yet.");
    return ok(architecture);
  }),
  route("GET", "/projects/:projectId/architecture/versions", (p, _b, db) =>
    ok(findProject(db, p.projectId).versionSummaries),
  ),
  route("GET", "/projects/:projectId/architecture/versions/:version", (p, _b, db) => {
    const architecture = findProject(db, p.projectId).versions.find((v) => String(v.version) === p.version);
    if (!architecture)
      throw new MockHttpError(404, "version_not_found", `Version ${p.version} does not exist.`);
    return ok(architecture);
  }),
  route("POST", "/projects/:projectId/architecture/commands", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const { baseVersion, commands } = parseBody(SaveCommandsBodySchema, body);
    const current = assertBaseVersion(record, baseVersion);
    if (!commands.some(isSemanticCommand)) {
      throw new MockHttpError(
        422,
        "invalid_command",
        "Layout-only changes are saved through the layout endpoint.",
      );
    }
    const next = applyOrReject(current, commands);
    return ok(commitVersion(record, next, "user", summarizeCommands(commands, current)));
  }),
  route("PUT", "/projects/:projectId/architecture/layout", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const { baseVersion, positions } = parseBody(LayoutBodySchema, body);
    const current = assertBaseVersion(record, baseVersion);
    record.versions[record.versions.length - 1] = applyOrReject(current, [
      { type: "MOVE_COMPONENTS", positions },
    ]);
    return noContent();
  }),

  route("GET", "/projects/:projectId/capacity", (p, _b, db) => {
    const capacity = findProject(db, p.projectId).capacity;
    if (!capacity)
      throw new MockHttpError(404, "not_analyzed", "The current architecture has not been analyzed.");
    return ok(capacity);
  }),
  route("POST", "/projects/:projectId/capacity/analyze", (p, _b, db) => {
    const record = findProject(db, p.projectId);
    const architecture = requireArchitecture(record);
    const capacity = mockCapacity(record, architecture);
    record.capacity = record.baselineCapacity = capacity;
    rememberVersionMetrics(record, architecture.version, {
      maxDailyActiveUsers: capacity.envelope.maxSupportedDailyActiveUsers,
    });
    return ok(capacity);
  }),

  route("GET", "/projects/:projectId/validation", (p, _b, db) => {
    const validation = findProject(db, p.projectId).validation;
    if (!validation)
      throw new MockHttpError(404, "not_validated", "The current architecture has not been validated.");
    return ok(validation);
  }),
  route("POST", "/projects/:projectId/validation/run", (p, _b, db) => {
    const record = findProject(db, p.projectId);
    const report = mockValidation(record, requireArchitecture(record));
    record.validation = record.baselineValidation = report;
    return ok(report);
  }),
  route("PATCH", "/projects/:projectId/validation/findings/:findingId", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const { status } = parseBody(FindingStatusSchema, body);
    const finding = record.validation?.findings.find((f) => f.id === p.findingId);
    if (!finding)
      throw new MockHttpError(404, "finding_not_found", `Finding "${p.findingId}" does not exist.`);
    finding.status = status;
    return ok(finding);
  }),

  route("POST", "/projects/:projectId/proposals", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const input = parseBody(ProposalRequestSchema, body);
    assertBaseVersion(record, input.baseVersion);
    const draft = draftProposal(record, input.prompt, input.selectedNodeIds);
    return ok(storeProposal(db, record, input.prompt, input.baseVersion, draft), 201);
  }),
  route("POST", "/projects/:projectId/findings/:findingId/fix", (p, _b, db) => {
    const record = findProject(db, p.projectId);
    const architecture = requireArchitecture(record);
    const finding = record.validation?.findings.find((f) => f.id === p.findingId);
    if (!finding)
      throw new MockHttpError(404, "finding_not_found", `Finding "${p.findingId}" does not exist.`);
    const draft = fixDraft(architecture, finding);
    return ok(storeProposal(db, record, `Fix: ${finding.title}`, architecture.version, draft), 201);
  }),
  route("POST", "/proposals/:proposalId/apply", (p, body, db) => {
    const proposal = findProposal(db, p.proposalId);
    const { baseVersion } = parseBody(BaseVersionSchema, body);
    if (proposal.status !== "pending") {
      throw new MockHttpError(409, "proposal_not_pending", `This proposal was already ${proposal.status}.`);
    }
    if (proposal.kind !== "change" || proposal.changes.length === 0) {
      throw new MockHttpError(
        422,
        "proposal_has_no_changes",
        "This proposal only explains; there is nothing to apply.",
      );
    }
    const record = findProject(db, proposal.projectId);
    const current = assertBaseVersion(record, baseVersion);
    assertBaseVersion(record, proposal.baseVersion);
    const next = applyOrReject(current, changesToCommands(proposal.changes));
    const architecture = commitVersion(record, next, "ai", proposal.recommendation);
    proposal.status = "applied";
    return ok(architecture);
  }),
  route("POST", "/proposals/:proposalId/reject", (p, _b, db) => {
    const proposal = findProposal(db, p.proposalId);
    if (proposal.status !== "pending") {
      throw new MockHttpError(409, "proposal_not_pending", `This proposal was already ${proposal.status}.`);
    }
    proposal.status = "rejected";
    return ok(proposal);
  }),

  route("GET", "/evidence/:evidenceId", (p, _b, db) => {
    const evidence = p.evidenceId ? db.evidence[p.evidenceId] : undefined;
    if (!evidence)
      throw new MockHttpError(404, "evidence_not_found", `Evidence "${p.evidenceId}" does not exist.`);
    return ok(evidence);
  }),

  route("GET", "/projects/:projectId/decisions", (p, _b, db) => ok(findProject(db, p.projectId).decisions)),
  route("POST", "/projects/:projectId/decisions", (p, body, db) => {
    const record = findProject(db, p.projectId);
    const input = parseBody(DecisionInputSchema, body);
    const decision = {
      ...input,
      id: createId("adr"),
      number: Math.max(0, ...record.decisions.map((d) => d.number)) + 1,
      status: "proposed" as const,
      date: now().slice(0, 10),
    };
    record.decisions.push(decision);
    return ok(decision, 201);
  }),

  ...extendedRoutes,
];

function errorBody(code: string, message: string, requestId: string, details?: unknown) {
  return { error: { code, message, details, request_id: requestId } };
}

/** Route a request against the mock db. Errors are returned in the API error envelope. */
export function handleMockRequest(request: MockRequest, requestId: string): MockResponse {
  const matches = routes
    .map((r) => ({ r, match: r.pattern.exec(request.path) }))
    .filter((m): m is { r: Route; match: RegExpExecArray } => m.match !== null);
  const hit = matches.find((m) => m.r.method === request.method);
  if (!hit) {
    return matches.length > 0
      ? {
          status: 405,
          body: errorBody("method_not_allowed", `${request.method} is not allowed here.`, requestId),
        }
      : { status: 404, body: errorBody("not_found", `No mock route for ${request.path}.`, requestId) };
  }

  const params: Params = {};
  hit.r.keys.forEach((key, i) => {
    params[key] = decodeURIComponent(hit.match[i + 1] ?? "");
  });

  try {
    const response = hit.r.handler(params, request.body, getMockDb(), request.query ?? new URLSearchParams());
    saveMockDb();
    return response;
  } catch (error) {
    if (error instanceof MockHttpError) {
      return { status: error.status, body: errorBody(error.code, error.message, requestId, error.details) };
    }
    return {
      status: 500,
      body: errorBody(
        "internal_error",
        error instanceof Error ? error.message : "Mock handler failed.",
        requestId,
      ),
    };
  }
}
