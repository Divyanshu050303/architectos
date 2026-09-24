/**
 * MOCK FIXTURE DATA — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Hand-written example projects so the UI can be exercised before apps/api exists.
 * None of these numbers are real analysis: capacity figures, findings, scores and
 * evidence are illustrative fixtures, not output of the capacity or validation engines.
 */
import { applyCommands } from "@/features/architecture/utils/commands";
import type {
  Architecture,
  ArchitectureEdge,
  ArchitectureNode,
  ArchitectureVersionSummary,
  Assumption,
  Decision,
  Evidence,
  Proposal,
} from "@/types/architecture";
import type { CapacityAnalysis } from "@/types/capacity";
import type { CostEstimate } from "@/types/cost";
import type { DiscoveryConnector, DiscoveryConnectorKind, DriftReport } from "@/types/discovery";
import type { Evolution, MigrationPlan } from "@/types/evolution";
import type { ObservabilityAnalysis } from "@/types/observability";
import type { Requirements } from "@/types/project";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis } from "@/types/security";
import type { SimulationConfig, SimulationResult, SimulationScenario } from "@/types/simulation";
import type { Finding, ValidationReport } from "@/types/validation";

import { mockEdge, mockNode, SEED_TIME, utilization } from "./builders";
import {
  ANALYSIS_EVIDENCE,
  CONNECTORS,
  discoverySnapshots,
  FOOD_VERSION_METRICS,
  foodCost,
  foodDrift,
  foodEvolution,
  foodMigrations,
  foodObservability,
  foodReliability,
  foodScenarios,
  foodSecurity,
  foodStageArchitectures,
  type MockDiscoverySnapshot,
  type MockVersionMetrics,
  URL_VERSION_METRICS,
  urlCost,
  urlDrift,
  urlEvolution,
  urlObservability,
  urlReliability,
  urlSecurity,
  urlStageArchitectures,
} from "./fixtures-analysis";
import { LARGE_PROJECT_ID, largeArchitecture, largeCapacity, largeValidation } from "./fixtures-large";

export { mockEdge, mockNode } from "./builders";
export type { MockDiscoverySnapshot, MockVersionMetrics } from "./fixtures-analysis";

export interface MockProjectRecord {
  id: string;
  name: string;
  description: string;
  createdAt: string;
  updatedAt: string;
  requirements: Requirements;
  /** Every version, oldest first; the last one is current. */
  versions: Architecture[];
  versionSummaries: ArchitectureVersionSummary[];
  /** Result for the current version, or null when "not analyzed". */
  capacity: CapacityAnalysis | null;
  validation: ValidationReport | null;
  /** Last analysis ever produced; the mock re-derives new results from it. */
  baselineCapacity: CapacityAnalysis | null;
  baselineValidation: ValidationReport | null;
  decisions: Decision[];
  /** Same "current / baseline" pattern for the extended analyses. */
  reliability: ReliabilityAnalysis | null;
  security: SecurityAnalysis | null;
  observability: ObservabilityAnalysis | null;
  cost: CostEstimate | null;
  drift: DriftReport | null;
  baselineReliability: ReliabilityAnalysis | null;
  baselineSecurity: SecurityAnalysis | null;
  baselineObservability: ObservabilityAnalysis | null;
  baselineCost: CostEstimate | null;
  baselineDrift: DriftReport | null;
  /** Fixture scenarios; null means "derive from the current architecture". */
  scenarios: SimulationScenario[] | null;
  latestSimulationId: string | null;
  evolution: Evolution | null;
  /** Topology snapshots for planned evolution stages (no saved version yet). */
  stageArchitectures: Record<string, Architecture>;
  migrations: MigrationPlan[];
  /** Keyed by version number as a string. */
  versionMetrics: Record<string, MockVersionMetrics>;
}

export interface MockSimulationRecord {
  id: string;
  projectId: string;
  architectureVersion: number;
  config: SimulationConfig;
  startedAt: number;
  /** Filled once the run's steps have elapsed. */
  result: SimulationResult | null;
}

export interface MockDiscoveryRecord {
  id: string;
  projectId: string;
  connector: DiscoveryConnectorKind;
  startedAt: number;
  savedVersion: number | null;
}

export interface MockJobRecord {
  id: string;
  projectId: string;
  startedAt: number;
  resultVersion: number | null;
}

export interface MockDbState {
  schemaVersion: 2;
  projects: MockProjectRecord[];
  evidence: Record<string, Evidence>;
  proposals: Record<string, Proposal>;
  jobs: Record<string, MockJobRecord>;
  simulations: Record<string, MockSimulationRecord>;
  connectors: DiscoveryConnector[];
  /** What each connected source contains, keyed by connector kind. */
  discoverySnapshots: Record<string, MockDiscoverySnapshot>;
  discoveries: Record<string, MockDiscoveryRecord>;
}

// --- Food Delivery ----------------------------------------------------------

const A_001: Assumption = {
  id: "A-001",
  statement: "Peak traffic is about 3× the daily average: 2.4M DAU produce ~31K requests/s at dinner peak.",
  source: "user",
};
const A_003: Assumption = {
  id: "A-003",
  statement: "Reads outnumber writes roughly 3:1 at peak.",
  source: "ai",
};
const A_007: Assumption = {
  id: "A-007",
  statement: "Each service replica keeps a PostgreSQL connection pool of up to 60 connections.",
  source: "default",
};
const FOOD_ASSUMPTIONS: Assumption[] = [A_001, A_003, A_007];

function cite(assumption: Assumption): { id: string; statement: string } {
  return { id: assumption.id, statement: assumption.statement };
}

const FOOD_NODES: ArchitectureNode[] = [
  mockNode(
    "client",
    "client",
    "Mobile & Web",
    "React / iOS / Android",
    { x: 400, y: 0 },
    {},
    {
      description: "Customer, courier and restaurant apps",
      domain: "edge",
    },
  ),
  mockNode(
    "cdn",
    "cdn",
    "CDN",
    "CloudFront",
    { x: 100, y: 150 },
    { cacheTtlSeconds: 3600 },
    {
      description: "Static assets and menu images",
      domain: "edge",
    },
  ),
  mockNode(
    "lb",
    "load_balancer",
    "Load Balancer",
    "AWS ALB",
    { x: 400, y: 150 },
    { idleTimeoutSeconds: 60 },
    {
      domain: "edge",
    },
  ),
  mockNode(
    "api",
    "service",
    "API",
    "Node.js",
    { x: 400, y: 300 },
    { replicas: 3, cpu: "2 vCPU", memory: "4 GB" },
    { description: "Public REST API", domain: "core" },
  ),
  mockNode(
    "redis",
    "cache",
    "Redis",
    "Redis 7",
    { x: 740, y: 300 },
    { replicas: 1, memory: "8 GB" },
    {
      description: "Menu and session cache",
      domain: "data",
    },
  ),
  mockNode(
    "prometheus",
    "observability",
    "Prometheus",
    "Prometheus",
    { x: 1060, y: 300 },
    { scrapeIntervalSeconds: 15 },
    { domain: "platform" },
  ),
  mockNode(
    "order_service",
    "service",
    "Order Service",
    "Go",
    { x: 200, y: 450 },
    { replicas: 2, cpu: "2 vCPU", memory: "4 GB" },
    { description: "Order lifecycle", domain: "core" },
  ),
  mockNode(
    "payment_service",
    "service",
    "Payment Service",
    "Java",
    { x: 600, y: 450 },
    { replicas: 2, cpu: "2 vCPU", memory: "4 GB" },
    { description: "Charges and refunds", domain: "payments" },
  ),
  mockNode(
    "kafka",
    "queue",
    "Kafka",
    "Apache Kafka",
    { x: 0, y: 600 },
    { brokers: 3, partitions: 12 },
    {
      description: "Order events",
      domain: "fulfillment",
    },
  ),
  mockNode(
    "postgres",
    "database",
    "PostgreSQL",
    "PostgreSQL",
    { x: 400, y: 600 },
    { replicas: 1, cpu: "4 vCPU", memory: "16 GB", storage: "500 GB", maxConnections: 500 },
    { description: "Primary store", domain: "data" },
  ),
  mockNode(
    "payment_provider",
    "external",
    "Payment Provider",
    "Stripe",
    { x: 800, y: 600 },
    {},
    {
      description: "Card processing",
      domain: "payments",
    },
  ),
  mockNode(
    "dispatch_worker",
    "worker",
    "Dispatch Worker",
    "Go",
    { x: 0, y: 750 },
    { replicas: 2, cpu: "1 vCPU", memory: "2 GB" },
    { description: "Assigns couriers to orders", domain: "fulfillment" },
  ),
];

const FOOD_EDGES: ArchitectureEdge[] = [
  mockEdge("e_client_cdn", "client", "cdn", "HTTPS", { critical: false }),
  mockEdge("e_client_lb", "client", "lb", "HTTPS"),
  mockEdge("e_lb_api", "lb", "api", "HTTP"),
  mockEdge("e_api_redis", "api", "redis", "RESP", { critical: false }),
  mockEdge("e_api_order", "api", "order_service", "gRPC"),
  mockEdge("e_api_payment", "api", "payment_service", "gRPC", { label: "charge" }),
  mockEdge("e_order_postgres", "order_service", "postgres", "SQL"),
  mockEdge("e_payment_postgres", "payment_service", "postgres", "SQL"),
  mockEdge("e_order_kafka", "order_service", "kafka", "Kafka", {
    label: "order events",
    synchronous: false,
    critical: false,
  }),
  mockEdge("e_kafka_dispatch", "kafka", "dispatch_worker", "Kafka", { synchronous: false, critical: false }),
  mockEdge("e_dispatch_postgres", "dispatch_worker", "postgres", "SQL", { critical: false }),
  mockEdge("e_payment_provider", "payment_service", "payment_provider", "HTTPS"),
  mockEdge("e_prometheus_api", "prometheus", "api", "HTTP", {
    label: "scrape",
    synchronous: false,
    critical: false,
  }),
];

function foodVersions(): Architecture[] {
  const v3: Architecture = {
    id: "arch_food",
    projectId: "proj_food",
    version: 3,
    nodes: FOOD_NODES,
    edges: FOOD_EDGES,
    assumptions: FOOD_ASSUMPTIONS,
    createdAt: "2026-09-18T14:20:00.000Z",
    createdBy: "user",
  };
  const v2 = {
    ...applyCommands(v3, [{ type: "REMOVE_COMPONENTS", nodeIds: ["prometheus"] }]),
    version: 2,
    createdAt: "2026-09-10T11:05:00.000Z",
  };
  const v1: Architecture = {
    ...applyCommands(v2, [{ type: "REMOVE_COMPONENTS", nodeIds: ["kafka", "dispatch_worker"] }]),
    version: 1,
    createdAt: "2026-09-02T16:40:00.000Z",
    createdBy: "ai",
  };
  return [v1, v2, v3];
}

const FOOD_FINDINGS: Finding[] = [
  {
    id: "f_spof_postgres",
    ruleId: "availability.single_point_of_failure",
    category: "reliability",
    severity: "critical",
    title: "PostgreSQL is a single point of failure",
    location: "PostgreSQL",
    whyItMatters:
      "A single primary with no replica means one instance failure stops ordering, payments and dispatch.",
    recommendation: "Run at least 2 replicas with automatic failover.",
    nodeIds: ["postgres"],
    edgeIds: [],
    evidenceIds: ["ev_pg_spof"],
    fixable: true,
    status: "open",
  },
  {
    id: "f_timeout_api_payment",
    ruleId: "resilience.missing_timeout",
    category: "reliability",
    severity: "high",
    title: "Missing timeout",
    location: "API → Payment Service",
    whyItMatters: "External dependency can block request workers.",
    recommendation: "Add timeout <= configured SLA.",
    nodeIds: ["api", "payment_service"],
    edgeIds: ["e_api_payment"],
    evidenceIds: ["ev_api_payment_timeout"],
    fixable: true,
    status: "open",
  },
  {
    id: "f_circuit_breaker_provider",
    ruleId: "resilience.missing_circuit_breaker",
    category: "reliability",
    severity: "high",
    title: "No circuit breaker on Payment Provider",
    location: "Payment Service → Payment Provider",
    whyItMatters: "When the provider degrades, retries pile up and exhaust Payment Service threads.",
    recommendation: "Add a circuit breaker with a fallback that queues payments for retry.",
    nodeIds: ["payment_service", "payment_provider"],
    edgeIds: ["e_payment_provider"],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_pg_connections",
    ruleId: "capacity.connection_saturation",
    category: "capacity",
    severity: "medium",
    title: "PostgreSQL connections above warning threshold",
    location: "PostgreSQL",
    whyItMatters: "At peak, new connections will be refused before CPU or storage become a limit.",
    recommendation: "Add a connection pooler such as PgBouncer or reduce per-replica pool sizes.",
    nodeIds: ["postgres"],
    edgeIds: [],
    evidenceIds: ["ev_pg_connections"],
    fixable: false,
    status: "open",
  },
  {
    id: "f_redis_no_replica",
    ruleId: "availability.cache_without_replica",
    category: "reliability",
    severity: "medium",
    title: "Redis has no replica",
    location: "Redis",
    whyItMatters: "A cache restart sends every read to PostgreSQL at once.",
    recommendation: "Add a replica or enable persistence so the cache warms quickly.",
    nodeIds: ["redis"],
    edgeIds: [],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_no_tracing",
    ruleId: "observability.no_distributed_tracing",
    category: "observability",
    severity: "medium",
    title: "No distributed tracing",
    location: "API, Order Service, Payment Service",
    whyItMatters: "Slow checkouts cannot be traced across service boundaries.",
    recommendation: "Propagate trace context and export spans to a tracing backend.",
    nodeIds: ["api", "order_service", "payment_service"],
    edgeIds: [],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_consumer_lag",
    ruleId: "observability.consumer_lag_unmonitored",
    category: "observability",
    severity: "medium",
    title: "Consumer lag is not monitored",
    location: "Kafka → Dispatch Worker",
    whyItMatters: "Dispatch delays go unnoticed until customers complain.",
    recommendation: "Alert on consumer group lag for the order events topic.",
    nodeIds: ["kafka", "dispatch_worker"],
    edgeIds: ["e_kafka_dispatch"],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_cdn_tls",
    ruleId: "security.legacy_tls",
    category: "security",
    severity: "low",
    title: "CDN accepts TLS 1.1",
    location: "CDN",
    whyItMatters: "Legacy TLS versions have known weaknesses.",
    recommendation: "Require TLS 1.2 or newer.",
    nodeIds: ["cdn"],
    edgeIds: ["e_client_cdn"],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_payment_overprovisioned",
    ruleId: "cost.overprovisioned",
    category: "cost",
    severity: "low",
    title: "Payment Service is over-provisioned",
    location: "Payment Service",
    whyItMatters: "Peak CPU stays under 40%, so part of the spend buys idle capacity.",
    recommendation: "Use smaller instances or enable autoscaling.",
    nodeIds: ["payment_service"],
    edgeIds: [],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_order_autoscaling",
    ruleId: "cost.no_autoscaling",
    category: "cost",
    severity: "low",
    title: "No autoscaling on Order Service",
    location: "Order Service",
    whyItMatters: "Replicas sized for dinner peak run idle most of the day.",
    recommendation: "Scale on CPU or request rate between 1 and 4 replicas.",
    nodeIds: ["order_service"],
    edgeIds: [],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
];

function foodCapacity(): CapacityAnalysis {
  return {
    projectId: "proj_food",
    architectureVersion: 3,
    calculatedAt: "2026-09-18T14:22:00.000Z",
    load: { dailyActiveUsers: 2_400_000, peakRps: 31_000, writesPerSecond: 8_200 },
    utilization: [
      utilization("api", "cpu", 61, 100, "%", 0.75, "ev_api_cpu"),
      utilization("redis", "memory", 3.36, 8, "GB", 0.8, "ev_redis_memory"),
      utilization("postgres", "connections", 410, 500, "connections", 0.7, "ev_pg_connections"),
      utilization("postgres", "reads", 8_200, 12_000, "reads/s", 0.75, "ev_pg_reads"),
      utilization("postgres", "writes", 2_100, 4_000, "writes/s", 0.75, "ev_pg_writes"),
      utilization("kafka", "throughput", 31, 100, "MB/s", 0.75, "ev_kafka_throughput"),
      utilization("order_service", "cpu", 55, 100, "%", 0.75),
      utilization("payment_service", "cpu", 38, 100, "%", 0.75),
    ],
    edges: [
      { edgeId: "e_client_cdn", rps: 12_000 },
      { edgeId: "e_client_lb", rps: 31_000 },
      { edgeId: "e_lb_api", rps: 31_000 },
      { edgeId: "e_api_redis", rps: 18_000 },
      { edgeId: "e_api_order", rps: 9_400 },
      { edgeId: "e_api_payment", rps: 2_100 },
      { edgeId: "e_order_postgres", rps: 6_200 },
      { edgeId: "e_payment_postgres", rps: 1_800 },
      { edgeId: "e_order_kafka", rps: 3_100 },
      { edgeId: "e_kafka_dispatch", rps: 3_100 },
      { edgeId: "e_payment_provider", rps: 2_100 },
    ],
    bottleneck: {
      nodeId: "postgres",
      resource: "connections",
      description: "PostgreSQL connections reach the configured maximum of 500.",
      thresholdDailyActiveUsers: 7_800_000,
      evidenceId: "ev_pg_bottleneck",
    },
    envelope: {
      maxSupportedDailyActiveUsers: 7_800_000,
      points: [
        { label: "100K", dailyActiveUsers: 100_000, status: "supported" },
        { label: "1M", dailyActiveUsers: 1_000_000, status: "supported" },
        { label: "2.4M", dailyActiveUsers: 2_400_000, status: "current" },
        { label: "5M", dailyActiveUsers: 5_000_000, status: "supported" },
        { label: "7.8M", dailyActiveUsers: 7_800_000, status: "warning" },
        { label: "10M", dailyActiveUsers: 10_000_000, status: "exceeded" },
      ],
    },
  };
}

function foodValidation(): ValidationReport {
  const ids = (category: Finding["category"]) =>
    FOOD_FINDINGS.filter((f) => f.category === category).map((f) => f.id);
  return {
    projectId: "proj_food",
    architectureVersion: 3,
    validatedAt: "2026-09-18T14:23:00.000Z",
    findings: FOOD_FINDINGS,
    health: {
      overall: 87,
      categories: [
        {
          category: "capacity",
          score: 91,
          findingIds: ids("capacity"),
          summary: "Headroom to ~7.8M DAU; PostgreSQL connections are above the warning threshold.",
        },
        {
          category: "reliability",
          score: 84,
          findingIds: ids("reliability"),
          summary: "PostgreSQL is a single point of failure and the payment path lacks timeouts.",
        },
        {
          category: "security",
          score: 93,
          findingIds: ids("security"),
          summary: "One low-severity TLS configuration issue at the edge.",
        },
        {
          category: "observability",
          score: 72,
          findingIds: ids("observability"),
          summary: "Metrics exist, but there is no tracing and no consumer-lag alerting.",
        },
        {
          category: "cost",
          score: 81,
          findingIds: ids("cost"),
          summary: "Some services are sized for peak without autoscaling.",
        },
      ],
    },
  };
}

const FOOD_DECISIONS: Decision[] = [
  {
    id: "adr_food_1",
    number: 1,
    title: "PostgreSQL as primary store",
    status: "accepted",
    date: "2026-09-02",
    context: "Orders and payments need transactions and relational integrity.",
    decision: "Use a managed PostgreSQL instance as the system of record.",
    consequences: "Connection limits must be managed as traffic grows.",
    relatedNodeIds: ["postgres"],
    sourceFindingId: null,
  },
  {
    id: "adr_food_2",
    number: 2,
    title: "Kafka for order events",
    status: "accepted",
    date: "2026-09-10",
    context: "Dispatch must not slow down order placement.",
    decision: "Publish order events to Kafka and consume them in the Dispatch Worker.",
    consequences: "Dispatch becomes eventually consistent; consumer lag needs monitoring.",
    relatedNodeIds: ["kafka", "order_service", "dispatch_worker"],
    sourceFindingId: null,
  },
];

// --- URL Shortener ----------------------------------------------------------

const URL_ARCHITECTURE: Architecture = {
  id: "arch_url",
  projectId: "proj_url",
  version: 1,
  nodes: [
    mockNode("client", "client", "Browser & API clients", "HTTP", { x: 300, y: 0 }, {}, { domain: "edge" }),
    mockNode("lb", "load_balancer", "Load Balancer", "NGINX", { x: 300, y: 150 }, {}, { domain: "edge" }),
    mockNode(
      "api",
      "service",
      "API",
      "Go",
      { x: 300, y: 300 },
      { replicas: 3, cpu: "1 vCPU", memory: "2 GB" },
      { description: "Shorten and redirect", domain: "core" },
    ),
    mockNode(
      "redis",
      "cache",
      "Redis",
      "Redis 7",
      { x: 560, y: 450 },
      { replicas: 2, memory: "4 GB" },
      {
        description: "Hot links",
        domain: "data",
      },
    ),
    mockNode(
      "postgres",
      "database",
      "PostgreSQL",
      "PostgreSQL",
      { x: 300, y: 450 },
      { replicas: 2, cpu: "2 vCPU", memory: "8 GB", storage: "100 GB", maxConnections: 300 },
      { description: "Link store", domain: "data" },
    ),
  ],
  edges: [
    mockEdge("e_client_lb", "client", "lb", "HTTPS"),
    mockEdge("e_lb_api", "lb", "api", "HTTP"),
    mockEdge("e_api_redis", "api", "redis", "RESP"),
    mockEdge("e_api_postgres", "api", "postgres", "SQL"),
  ],
  assumptions: [{ id: "A-001", statement: "Redirects outnumber new links 100:1.", source: "user" }],
  createdAt: "2026-08-20T10:00:00.000Z",
  createdBy: "ai",
};

const URL_FINDINGS: Finding[] = [
  {
    id: "f_url_rate_limit",
    ruleId: "security.no_rate_limiting",
    category: "security",
    severity: "low",
    title: "No rate limiting on link creation",
    location: "Load Balancer → API",
    whyItMatters: "Anonymous clients can create links in bulk for spam.",
    recommendation: "Rate limit POST /links per client IP.",
    nodeIds: ["lb", "api"],
    edgeIds: ["e_lb_api"],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
  {
    id: "f_url_click_writes",
    ruleId: "capacity.synchronous_analytics_writes",
    category: "capacity",
    severity: "low",
    title: "Click analytics are written synchronously",
    location: "API → PostgreSQL",
    whyItMatters: "Every redirect waits for an analytics insert.",
    recommendation: "Buffer click events and write them in batches.",
    nodeIds: ["api", "postgres"],
    edgeIds: ["e_api_postgres"],
    evidenceIds: [],
    fixable: false,
    status: "open",
  },
];

function urlCapacity(): CapacityAnalysis {
  return {
    projectId: "proj_url",
    architectureVersion: 1,
    calculatedAt: "2026-08-20T10:05:00.000Z",
    load: { dailyActiveUsers: 500_000, peakRps: 4_000, writesPerSecond: 60 },
    utilization: [
      utilization("api", "cpu", 34, 100, "%", 0.75),
      utilization("redis", "memory", 0.88, 4, "GB", 0.8),
      utilization("postgres", "connections", 90, 300, "connections", 0.7),
    ],
    edges: [
      { edgeId: "e_client_lb", rps: 4_000 },
      { edgeId: "e_lb_api", rps: 4_000 },
      { edgeId: "e_api_redis", rps: 3_600 },
      { edgeId: "e_api_postgres", rps: 460 },
    ],
    bottleneck: {
      nodeId: "api",
      resource: "cpu",
      description: "API CPU reaches the warning threshold.",
      thresholdDailyActiveUsers: 6_000_000,
      evidenceId: null,
    },
    envelope: {
      maxSupportedDailyActiveUsers: 6_000_000,
      points: [
        { label: "100K", dailyActiveUsers: 100_000, status: "supported" },
        { label: "500K", dailyActiveUsers: 500_000, status: "current" },
        { label: "2M", dailyActiveUsers: 2_000_000, status: "supported" },
        { label: "6M", dailyActiveUsers: 6_000_000, status: "warning" },
        { label: "10M", dailyActiveUsers: 10_000_000, status: "exceeded" },
      ],
    },
  };
}

function urlValidation(): ValidationReport {
  return {
    projectId: "proj_url",
    architectureVersion: 1,
    validatedAt: "2026-08-20T10:06:00.000Z",
    findings: URL_FINDINGS,
    health: {
      overall: 96,
      categories: [
        { category: "capacity", score: 97, findingIds: ["f_url_click_writes"], summary: "Large headroom." },
        { category: "reliability", score: 98, findingIds: [], summary: "Replicated data stores." },
        { category: "security", score: 95, findingIds: ["f_url_rate_limit"], summary: "Add rate limiting." },
        { category: "observability", score: 94, findingIds: [], summary: "Basic metrics in place." },
        { category: "cost", score: 96, findingIds: [], summary: "Right-sized." },
      ],
    },
  };
}

// --- Evidence ---------------------------------------------------------------

const EVIDENCE: Evidence[] = [
  {
    id: "ev_pg_connections",
    claim: "PostgreSQL is approaching connection capacity",
    kind: "calculation",
    calculations: [
      { label: "Expected connections", value: 438 },
      { label: "Configured maximum", value: 500 },
      { label: "Warning threshold", value: 350 },
    ],
    source: "Capacity model CM-182",
    assumptions: [cite(A_001), cite(A_007)],
  },
  {
    id: "ev_pg_bottleneck",
    claim: "PostgreSQL connections are the next bottleneck at ~7.8M DAU",
    kind: "calculation",
    calculations: [
      { label: "Connections per 1M DAU", value: 64 },
      { label: "Configured maximum", value: 500 },
      { label: "Threshold", value: "7.8M", unit: "DAU" },
    ],
    source: "Capacity model CM-182",
    assumptions: [cite(A_007)],
  },
  {
    id: "ev_api_cpu",
    claim: "API CPU is at 61% at peak",
    kind: "benchmark",
    calculations: [
      { label: "Peak requests", value: 31_000, unit: "req/s" },
      { label: "Requests per vCPU", value: 8_500, unit: "req/s" },
      { label: "Provisioned vCPU", value: 6 },
    ],
    source: "Node.js HTTP benchmark B-014",
    assumptions: [cite(A_001)],
  },
  {
    id: "ev_redis_memory",
    claim: "Redis uses 42% of its memory",
    kind: "calculation",
    calculations: [
      { label: "Cached keys", value: 1_400_000 },
      { label: "Average value size", value: 2.4, unit: "KB" },
      { label: "Memory limit", value: 8, unit: "GB" },
    ],
    source: "Capacity model CM-182",
    assumptions: [],
  },
  {
    id: "ev_pg_reads",
    claim: "PostgreSQL serves 8.2K reads/s of a 12K reads/s budget",
    kind: "calculation",
    calculations: [
      { label: "Reads after cache", value: 8_200, unit: "/s" },
      { label: "Read budget", value: 12_000, unit: "/s" },
    ],
    source: "Capacity model CM-182",
    assumptions: [cite(A_003)],
  },
  {
    id: "ev_pg_writes",
    claim: "PostgreSQL handles 2.1K writes/s of a 4K writes/s budget",
    kind: "calculation",
    calculations: [
      { label: "Writes", value: 2_100, unit: "/s" },
      { label: "Write budget", value: 4_000, unit: "/s" },
    ],
    source: "Capacity model CM-182",
    assumptions: [cite(A_003)],
  },
  {
    id: "ev_kafka_throughput",
    claim: "Kafka uses 31% of its throughput",
    kind: "constraint",
    calculations: [
      { label: "Produced", value: 31, unit: "MB/s" },
      { label: "Cluster limit (3 brokers)", value: 100, unit: "MB/s" },
    ],
    source: "Component constraint KAFKA-THROUGHPUT",
    assumptions: [],
  },
  {
    id: "ev_pg_spof",
    claim: "PostgreSQL has a single instance",
    kind: "rule",
    calculations: [
      { label: "Replicas", value: 1 },
      { label: "Required for availability target", value: 2 },
    ],
    source: "Rule availability.single_point_of_failure",
    assumptions: [],
  },
  {
    id: "ev_api_payment_timeout",
    claim: "API calls Payment Service without a timeout",
    kind: "rule",
    calculations: [
      { label: "Configured timeout", value: "none" },
      { label: "P99 latency target", value: 300, unit: "ms" },
    ],
    source: "Rule resilience.missing_timeout",
    assumptions: [],
  },
  {
    id: "ev_cache_reads",
    claim: "A read cache removes about two thirds of database reads",
    kind: "calculation",
    calculations: [
      { label: "Database reads today", value: 18_000, unit: "/s" },
      { label: "Expected cache hit ratio", value: "67%" },
      { label: "Database reads with cache", value: 6_000, unit: "/s" },
    ],
    source: "Mock capacity template",
    assumptions: [cite(A_003)],
  },
  {
    id: "ev_cache_cost",
    claim: "A Redis node adds about $37/month",
    kind: "constraint",
    calculations: [
      { label: "Current estimate", value: 1240, unit: "USD/month" },
      { label: "Redis node (8 GB)", value: 37, unit: "USD/month" },
    ],
    source: "Mock price list",
    assumptions: [],
  },
  {
    id: "ev_queue_decoupling",
    claim: "A queue decouples the caller from the callee's latency",
    kind: "rule",
    calculations: [{ label: "Synchronous hops removed", value: 1 }],
    source: "Mock rule messaging.async_decoupling",
    assumptions: [],
  },
];

// --- Seed -------------------------------------------------------------------

export function emptyRequirements(projectId: string): Requirements {
  return {
    projectId,
    description: "",
    functional: [],
    nonFunctional: {
      dailyActiveUsers: null,
      peakRps: null,
      availabilityTarget: null,
      p99LatencyMs: null,
      dataRetentionDays: null,
      regions: [],
    },
    updatedAt: null,
  };
}

/** A project with nothing analyzed yet (also used by POST /projects). */
export function newProjectRecord(
  id: string,
  name: string,
  description: string,
  timestamp: string,
): MockProjectRecord {
  return {
    id,
    name,
    description,
    createdAt: timestamp,
    updatedAt: timestamp,
    requirements: emptyRequirements(id),
    versions: [],
    versionSummaries: [],
    capacity: null,
    validation: null,
    baselineCapacity: null,
    baselineValidation: null,
    decisions: [],
    reliability: null,
    security: null,
    observability: null,
    cost: null,
    drift: null,
    baselineReliability: null,
    baselineSecurity: null,
    baselineObservability: null,
    baselineCost: null,
    baselineDrift: null,
    scenarios: null,
    latestSimulationId: null,
    evolution: null,
    stageArchitectures: {},
    migrations: [],
    versionMetrics: {},
  };
}

/** A seeded simulation that already finished, so the simulation view has a latest run. */
const FOOD_SEED_SIMULATION: MockSimulationRecord = {
  id: "sim_food_pg_failure",
  projectId: "proj_food",
  architectureVersion: 3,
  config: {
    scenarioId: "scn_pg_failure",
    traffic: "current",
    durationMinutes: 5,
    environment: "production_like",
  },
  startedAt: Date.parse("2026-09-18T14:30:00.000Z"),
  result: null,
};

/** A fresh, deep copy of the seed state. */
export function createSeedState(): MockDbState {
  const food = foodVersions();
  const foodCurrent = food[food.length - 1] as Architecture;
  const foodCap = foodCapacity();
  const foodVal = foodValidation();
  const urlCap = urlCapacity();
  const urlVal = urlValidation();
  const large = largeArchitecture();
  const largeCap = largeCapacity(large);
  const largeVal = largeValidation();

  const foodRecord: MockProjectRecord = {
    ...newProjectRecord(
      "proj_food",
      "Food Delivery",
      "Order, pay and dispatch food deliveries across the US East region.",
      "2026-09-02T16:30:00.000Z",
    ),
    updatedAt: "2026-09-18T14:23:00.000Z",
    requirements: {
      projectId: "proj_food",
      description:
        "A food delivery platform where customers browse restaurants, place orders and pay, " +
        "and couriers are dispatched in real time. Menus are read heavily; order events drive dispatch.",
      functional: [
        "Browse restaurants and menus",
        "Place and pay for orders",
        "Dispatch couriers to orders",
        "Track order status in real time",
      ],
      nonFunctional: {
        dailyActiveUsers: 2_400_000,
        peakRps: 31_000,
        availabilityTarget: 0.9995,
        p99LatencyMs: 300,
        dataRetentionDays: 365,
        regions: ["us-east-1"],
      },
      updatedAt: "2026-09-02T16:35:00.000Z",
    },
    versions: food,
    versionSummaries: [
      {
        version: 1,
        createdAt: food[0]?.createdAt ?? SEED_TIME,
        createdBy: "ai",
        summary: "Generated from requirements",
      },
      {
        version: 2,
        createdAt: food[1]?.createdAt ?? SEED_TIME,
        createdBy: "user",
        summary: "Added Kafka and Dispatch Worker for order events",
      },
      {
        version: 3,
        createdAt: food[2]?.createdAt ?? SEED_TIME,
        createdBy: "user",
        summary: "Added Prometheus monitoring",
      },
    ],
    capacity: foodCap,
    validation: foodVal,
    baselineCapacity: foodCap,
    baselineValidation: foodVal,
    decisions: FOOD_DECISIONS,
    reliability: foodReliability(),
    security: foodSecurity(),
    observability: foodObservability(),
    cost: foodCost(),
    drift: foodDrift(),
    baselineReliability: foodReliability(),
    baselineSecurity: foodSecurity(),
    baselineObservability: foodObservability(),
    baselineCost: foodCost(),
    baselineDrift: foodDrift(),
    scenarios: foodScenarios(foodCurrent),
    latestSimulationId: FOOD_SEED_SIMULATION.id,
    evolution: foodEvolution(),
    stageArchitectures: foodStageArchitectures(foodCurrent),
    migrations: foodMigrations(),
    versionMetrics: FOOD_VERSION_METRICS,
  };

  const urlRecord: MockProjectRecord = {
    ...newProjectRecord(
      "proj_url",
      "URL Shortener",
      "Short links with click analytics.",
      "2026-08-20T09:50:00.000Z",
    ),
    updatedAt: "2026-08-20T10:06:00.000Z",
    requirements: {
      projectId: "proj_url",
      description:
        "A URL shortener that creates short links, redirects quickly from a cache and records clicks.",
      functional: ["Create short links", "Redirect to the original URL", "Show click counts"],
      nonFunctional: {
        dailyActiveUsers: 500_000,
        peakRps: 4_000,
        availabilityTarget: 0.999,
        p99LatencyMs: 100,
        dataRetentionDays: null,
        regions: ["eu-west-1"],
      },
      updatedAt: "2026-08-20T09:55:00.000Z",
    },
    versions: [URL_ARCHITECTURE],
    versionSummaries: [
      {
        version: 1,
        createdAt: URL_ARCHITECTURE.createdAt,
        createdBy: "ai",
        summary: "Generated from requirements",
      },
    ],
    capacity: urlCap,
    validation: urlVal,
    baselineCapacity: urlCap,
    baselineValidation: urlVal,
    reliability: urlReliability(),
    security: urlSecurity(),
    observability: urlObservability(),
    cost: urlCost(),
    drift: urlDrift(),
    baselineReliability: urlReliability(),
    baselineSecurity: urlSecurity(),
    baselineObservability: urlObservability(),
    baselineCost: urlCost(),
    baselineDrift: urlDrift(),
    evolution: urlEvolution(),
    stageArchitectures: urlStageArchitectures(URL_ARCHITECTURE),
    versionMetrics: URL_VERSION_METRICS,
  };

  const largeRecord: MockProjectRecord = {
    ...newProjectRecord(
      LARGE_PROJECT_ID,
      "Marketplace (large)",
      "A multi-domain marketplace with ~120 components, for large-graph views.",
      "2026-09-12T11:50:00.000Z",
    ),
    updatedAt: "2026-09-12T12:05:00.000Z",
    requirements: {
      projectId: LARGE_PROJECT_ID,
      description:
        "A multi-seller marketplace: catalog and search, cart and checkout, payments and payouts, " +
        "fulfillment with carriers, notifications and analytics.",
      functional: [
        "Browse and search listings",
        "Check out and pay",
        "Ship and track orders",
        "Pay out sellers",
      ],
      nonFunctional: {
        dailyActiveUsers: 20_000_000,
        peakRps: 180_000,
        availabilityTarget: 0.9995,
        p99LatencyMs: 250,
        dataRetentionDays: 730,
        regions: ["us-east-1", "eu-west-1"],
      },
      updatedAt: "2026-09-12T11:55:00.000Z",
    },
    versions: [large],
    versionSummaries: [
      {
        version: 1,
        createdAt: large.createdAt,
        createdBy: "ai",
        summary: "Generated demo architecture (120 components)",
      },
    ],
    capacity: largeCap,
    validation: largeVal,
    baselineCapacity: largeCap,
    baselineValidation: largeVal,
    versionMetrics: {
      "1": { maxDailyActiveUsers: largeCap.envelope.maxSupportedDailyActiveUsers, monthlyCost: null },
    },
  };

  const state: MockDbState = {
    schemaVersion: 2,
    projects: [
      foodRecord,
      urlRecord,
      newProjectRecord("proj_pay", "Payments Platform", "Not started yet.", "2026-09-20T08:00:00.000Z"),
      largeRecord,
    ],
    evidence: Object.fromEntries([...EVIDENCE, ...ANALYSIS_EVIDENCE].map((e) => [e.id, e])),
    proposals: {},
    jobs: {},
    simulations: { [FOOD_SEED_SIMULATION.id]: FOOD_SEED_SIMULATION },
    connectors: CONNECTORS,
    discoverySnapshots: discoverySnapshots(foodCurrent),
    discoveries: {},
  };
  return structuredClone(state);
}
