/**
 * MOCK FIXTURE DATA — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * Reliability, security, observability, cost, drift, evolution, migration, simulation
 * scenario and discovery fixtures for the example projects. None of these numbers are
 * real analysis: they are hand-written illustrations kept consistent with the Food
 * Delivery architecture, capacity and validation fixtures in fixtures.ts.
 */
import { applyCommands } from "@/features/architecture/utils/commands";
import type { Architecture, Evidence } from "@/types/architecture";
import type { CostEstimate, NodeCost } from "@/types/cost";
import type {
  DiscoveredResource,
  DiscoveryConnector,
  DiscoveryConnectorKind,
  DriftReport,
} from "@/types/discovery";
import type { Evolution, MigrationPlan } from "@/types/evolution";
import type { ObservabilityAnalysis, ObservabilityCoverage, TelemetrySignal } from "@/types/observability";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis, SecurityControls } from "@/types/security";
import type { SimulationScenario } from "@/types/simulation";

import { mockEdge, mockNode } from "./builders";

/** Headline figures remembered per architecture version (for comparisons). */
export interface MockVersionMetrics {
  maxDailyActiveUsers: number | null;
  monthlyCost: number | null;
}

/** What a connected source "contains" in mock mode: its resources and the architecture they map to. */
export interface MockDiscoverySnapshot {
  resources: DiscoveredResource[];
  architecture: Architecture | null;
}

const FOOD_ANALYZED_AT = "2026-09-18T14:24:00.000Z";
const URL_ANALYZED_AT = "2026-08-20T10:07:00.000Z";

// --- Shared helpers (fixture construction only) -----------------------------

const TELEMETRY: readonly TelemetrySignal[] = ["metrics", "logs", "traces", "alerts", "dashboards"];

function coverage(nodeId: string, flags: string): ObservabilityCoverage {
  // flags: five 0/1 characters in TELEMETRY order, e.g. "11011"
  const on = (i: number) => flags[i] === "1";
  return { nodeId, metrics: on(0), logs: on(1), traces: on(2), alerts: on(3), dashboards: on(4) };
}

function missingSignals(row: ObservabilityCoverage): TelemetrySignal[] {
  return TELEMETRY.filter((signal) => !row[signal]);
}

function controls(nodeId: string, values: Partial<Omit<SecurityControls, "nodeId">> = {}): SecurityControls {
  return {
    nodeId,
    authentication: null,
    authorization: null,
    encryptionInTransit: null,
    encryptionAtRest: null,
    secretsManagement: null,
    handlesPii: false,
    ...values,
  };
}

function nodeCost(nodeId: string, breakdown: Array<[string, number]>): NodeCost {
  return {
    nodeId,
    monthly: breakdown.reduce((sum, [, monthly]) => sum + monthly, 0),
    breakdown: breakdown.map(([item, monthly]) => ({ item, monthly })),
  };
}

// --- Evidence ---------------------------------------------------------------

export const ANALYSIS_EVIDENCE: Evidence[] = [
  {
    id: "ev_availability_estimate",
    claim: "Estimated availability is 99.82%, below the 99.95% target",
    kind: "calculation",
    calculations: [
      { label: "Checkout path availability", value: "99.81%" },
      { label: "PostgreSQL (single instance)", value: "99.90%" },
      { label: "Target", value: "99.95%" },
      { label: "Downtime per month", value: 79, unit: "min" },
    ],
    source: "Reliability model RM-031",
    assumptions: [],
  },
  {
    id: "ev_cost_postgres",
    claim: "PostgreSQL costs $184/month",
    kind: "calculation",
    calculations: [
      { label: "db.m6g.xlarge on-demand", value: 138, unit: "USD/month" },
      { label: "500 GB gp3 storage", value: 46, unit: "USD/month" },
    ],
    source: "AWS price list (us-east-1), mock snapshot",
    assumptions: [{ id: "C-001", statement: "On-demand us-east-1 prices, 730 hours per month." }],
  },
  {
    id: "ev_cost_total",
    claim: "The architecture costs about $1,240/month",
    kind: "calculation",
    calculations: [
      { label: "Compute", value: 576, unit: "USD/month" },
      { label: "Messaging", value: 232, unit: "USD/month" },
      { label: "Database", value: 184, unit: "USD/month" },
      { label: "Networking", value: 102, unit: "USD/month" },
      { label: "Cache", value: 98, unit: "USD/month" },
      { label: "Observability", value: 48, unit: "USD/month" },
    ],
    source: "AWS price list (us-east-1), mock snapshot",
    assumptions: [{ id: "C-001", statement: "On-demand us-east-1 prices, 730 hours per month." }],
  },
  {
    id: "ev_threat_payment_egress",
    claim: "Payment Service reaches the provider over public internet egress",
    kind: "rule",
    calculations: [
      { label: "Egress path", value: "NAT gateway → internet" },
      { label: "Destination allowlist", value: "none" },
    ],
    source: "Rule security.unrestricted_egress",
    assumptions: [],
  },
  {
    id: "ev_sim_pg_failure",
    claim: "Losing PostgreSQL raises checkout P99 from 320 ms to 1.8 s",
    kind: "benchmark",
    calculations: [
      { label: "Connection retry budget", value: 3 },
      { label: "Retry backoff", value: 500, unit: "ms" },
      { label: "Requests failing fast", value: "12.4%" },
    ],
    source: "Mock failure-injection profile FI-007",
    assumptions: [],
  },
];

// --- Food Delivery: reliability ---------------------------------------------

export function foodReliability(): ReliabilityAnalysis {
  return {
    availability: { target: 0.9995, estimated: 0.9982, monthlyDowntimeMinutes: 79 },
    entrypoints: [
      { nodeId: "lb", availability: 0.9982 },
      { nodeId: "cdn", availability: 0.9999 },
    ],
    singlePointsOfFailure: [
      {
        nodeId: "postgres",
        reason: "Single primary with no replica or automatic failover.",
        dependentNodeIds: ["order_service", "payment_service", "dispatch_worker", "api"],
        severity: "critical",
        evidenceId: "ev_pg_spof",
      },
      {
        nodeId: "redis",
        reason: "Single cache node: a restart sends every menu read to PostgreSQL.",
        dependentNodeIds: ["api"],
        severity: "medium",
        evidenceId: null,
      },
    ],
    criticalPaths: [
      { nodeIds: ["client", "lb", "api", "payment_service", "payment_provider"], availability: 0.9981 },
      { nodeIds: ["client", "lb", "api", "payment_service", "postgres"], availability: 0.9983 },
      { nodeIds: ["client", "lb", "api", "order_service", "postgres"], availability: 0.9984 },
    ],
    cascadeRisks: [
      {
        edgeId: "e_api_payment",
        reasons: ["No timeout configured", "Synchronous call on the checkout path"],
      },
      {
        edgeId: "e_payment_provider",
        reasons: ["No circuit breaker", "External dependency outside your control"],
      },
      { edgeId: "e_order_postgres", reasons: ["Depends on a single-instance database"] },
      { edgeId: "e_payment_postgres", reasons: ["Depends on a single-instance database"] },
    ],
    criticalEdgeIds: [
      "e_client_lb",
      "e_lb_api",
      "e_api_order",
      "e_api_payment",
      "e_order_postgres",
      "e_payment_postgres",
      "e_payment_provider",
    ],
    analyzedAt: FOOD_ANALYZED_AT,
    architectureVersion: 3,
  };
}

// --- Food Delivery: security ------------------------------------------------

export function foodSecurity(): SecurityAnalysis {
  const service = { authentication: true, authorization: true, encryptionInTransit: false };
  return {
    score: 93,
    trustBoundaries: [
      { id: "tb_internet", name: "Internet", nodeIds: ["client", "cdn"] },
      { id: "tb_public_subnet", name: "Public subnet", nodeIds: ["lb"] },
      {
        id: "tb_app",
        name: "Private application subnet",
        nodeIds: ["api", "order_service", "payment_service", "dispatch_worker", "prometheus"],
      },
      { id: "tb_data", name: "Private data subnet", nodeIds: ["postgres", "redis", "kafka"] },
      { id: "tb_third_party", name: "Third party (Stripe)", nodeIds: ["payment_provider"] },
    ],
    exposure: [
      { nodeId: "client", level: "public", reason: "End-user devices on the public internet." },
      { nodeId: "cdn", level: "public", reason: "Serves static assets to anyone." },
      { nodeId: "lb", level: "public", reason: "Internet-facing HTTPS listener on port 443." },
      { nodeId: "api", level: "internal", reason: "Reachable only from the load balancer." },
      { nodeId: "order_service", level: "internal", reason: "Called by the API inside the VPC." },
      { nodeId: "payment_service", level: "internal", reason: "Called by the API inside the VPC." },
      { nodeId: "prometheus", level: "internal", reason: "Scrapes services inside the VPC." },
      { nodeId: "dispatch_worker", level: "private", reason: "No inbound traffic; consumes Kafka only." },
      { nodeId: "postgres", level: "private", reason: "Private subnet, no public endpoint." },
      { nodeId: "redis", level: "private", reason: "Private subnet, no public endpoint." },
      { nodeId: "kafka", level: "private", reason: "Private subnet, no public endpoint." },
      {
        nodeId: "payment_provider",
        level: "public",
        reason: "Public egress: Payment Service calls the Stripe API over the internet.",
      },
    ],
    controls: [
      controls("cdn", { encryptionInTransit: true }),
      controls("lb", { encryptionInTransit: true }),
      controls("api", { ...service, secretsManagement: true, handlesPii: true }),
      controls("order_service", { ...service, secretsManagement: true, handlesPii: true }),
      controls("payment_service", { ...service, secretsManagement: true, handlesPii: true }),
      controls("dispatch_worker", { authentication: true, secretsManagement: true, handlesPii: true }),
      controls("postgres", {
        authentication: true,
        authorization: true,
        encryptionInTransit: true,
        encryptionAtRest: true,
        secretsManagement: true,
        handlesPii: true,
      }),
      controls("redis", {
        authentication: true,
        encryptionInTransit: false,
        encryptionAtRest: false,
        handlesPii: true,
      }),
      controls("kafka", {
        authentication: true,
        encryptionInTransit: false,
        encryptionAtRest: null,
        handlesPii: true,
      }),
      controls("prometheus", { authentication: false }),
    ],
    threats: [
      {
        id: "thr_payment_egress",
        title: "Payment data leaves through unrestricted public egress",
        category: "information_disclosure",
        severity: "medium",
        nodeIds: ["payment_service", "payment_provider"],
        mitigation: "Tokenize card data and restrict egress to the provider's published IP ranges.",
        evidenceId: "ev_threat_payment_egress",
      },
      {
        id: "thr_kafka_plaintext",
        title: "Order events with addresses travel unencrypted",
        category: "information_disclosure",
        severity: "medium",
        nodeIds: ["kafka", "order_service", "dispatch_worker"],
        mitigation: "Enable TLS between clients and brokers and encrypt topics at rest.",
        evidenceId: null,
      },
      {
        id: "thr_api_flood",
        title: "Checkout API can be flooded without rate limits",
        category: "denial_of_service",
        severity: "medium",
        nodeIds: ["lb", "api"],
        mitigation: "Add WAF rate-based rules in front of the load balancer.",
        evidenceId: null,
      },
      {
        id: "thr_service_spoofing",
        title: "Internal gRPC calls are not mutually authenticated",
        category: "spoofing",
        severity: "low",
        nodeIds: ["api", "order_service", "payment_service"],
        mitigation: "Use mTLS between services (service mesh or SPIFFE identities).",
        evidenceId: null,
      },
      {
        id: "thr_cdn_tls",
        title: "Downgrade to TLS 1.1 at the CDN",
        category: "tampering",
        severity: "low",
        nodeIds: ["cdn"],
        mitigation: "Require TLS 1.2 or newer on the distribution.",
        evidenceId: null,
      },
      {
        id: "thr_refund_audit",
        title: "Refunds are not written to an audit log",
        category: "repudiation",
        severity: "low",
        nodeIds: ["payment_service"],
        mitigation: "Record every refund with actor, amount and reason in an append-only log.",
        evidenceId: null,
      },
    ],
    analyzedAt: FOOD_ANALYZED_AT,
    architectureVersion: 3,
  };
}

// --- Food Delivery: observability -------------------------------------------

const GAP_RECOMMENDATIONS: Record<TelemetrySignal, string> = {
  metrics: "export RED metrics",
  logs: "ship structured logs",
  traces: "propagate trace context",
  alerts: "alert on saturation and errors",
  dashboards: "add a service dashboard",
};

export function observabilityGaps(rows: readonly ObservabilityCoverage[]) {
  return rows
    .map((row) => ({ nodeId: row.nodeId, missing: missingSignals(row) }))
    .filter((gap) => gap.missing.length > 0)
    .map((gap) => {
      const text = gap.missing.map((m) => GAP_RECOMMENDATIONS[m]).join(", ");
      return { ...gap, recommendation: text.charAt(0).toUpperCase() + text.slice(1) + "." };
    });
}

export function foodObservability(): ObservabilityAnalysis {
  const rows = [
    coverage("lb", "11011"),
    coverage("cdn", "11000"),
    coverage("api", "11011"),
    coverage("order_service", "11001"),
    coverage("payment_service", "11011"),
    coverage("dispatch_worker", "11000"),
    coverage("postgres", "11011"),
    coverage("redis", "10001"),
    coverage("kafka", "10000"),
    coverage("prometheus", "11011"),
  ];
  return {
    score: 72,
    coverage: rows,
    slos: [
      {
        id: "slo_checkout",
        name: "Checkout success rate",
        nodeIds: ["api", "payment_service"],
        target: 0.999,
        current: 0.9986,
        errorBudgetRemaining: 0.42,
        window: "30d",
      },
      {
        id: "slo_menu_latency",
        name: "Menu reads under 300 ms (P99)",
        nodeIds: ["api", "redis"],
        target: 0.99,
        current: 0.993,
        errorBudgetRemaining: 0.71,
        window: "30d",
      },
      {
        id: "slo_dispatch",
        name: "Courier assigned within 60 s",
        nodeIds: ["kafka", "dispatch_worker"],
        target: 0.95,
        current: 0.938,
        errorBudgetRemaining: 0,
        window: "7d",
      },
    ],
    gaps: observabilityGaps(rows),
    analyzedAt: FOOD_ANALYZED_AT,
    architectureVersion: 3,
  };
}

// --- Food Delivery: cost (spec §71: PostgreSQL $184/mo, total $1,240/mo) ------

export const COST_CATEGORY_BY_TYPE: Partial<Record<Architecture["nodes"][number]["type"], string>> = {
  service: "Compute",
  worker: "Compute",
  gateway: "Networking",
  load_balancer: "Networking",
  cdn: "Networking",
  database: "Database",
  cache: "Cache",
  queue: "Messaging",
  storage: "Storage",
  observability: "Observability",
};

export const COST_ASSUMPTIONS = [
  { id: "C-001", statement: "On-demand us-east-1 prices, 730 hours per month." },
  { id: "C-002", statement: "About 2 TB/month of data transfer out, billed under the CDN." },
  { id: "C-003", statement: "Reserved-instance and savings-plan discounts are not applied." },
];

export function foodCost(): CostEstimate {
  const nodes = [
    nodeCost("api", [["3 × Fargate task (2 vCPU, 4 GB)", 216]]),
    nodeCost("order_service", [["2 × Fargate task (2 vCPU, 4 GB)", 144]]),
    nodeCost("payment_service", [["2 × Fargate task (2 vCPU, 4 GB)", 144]]),
    nodeCost("dispatch_worker", [["2 × Fargate task (1 vCPU, 2 GB)", 72]]),
    nodeCost("postgres", [
      ["db.m6g.xlarge instance", 138],
      ["500 GB gp3 storage", 46],
    ]),
    nodeCost("redis", [["cache.r6g.large node", 98]]),
    nodeCost("kafka", [
      ["3 × kafka.m5.large broker", 186],
      ["Broker storage (3 × 100 GB)", 46],
    ]),
    nodeCost("lb", [
      ["Load balancer hours", 17],
      ["Load balancer capacity units", 21],
    ]),
    nodeCost("cdn", [
      ["Data transfer out (2 TB)", 52],
      ["HTTPS requests", 12],
    ]),
    nodeCost("prometheus", [["Managed Prometheus ingestion", 48]]),
  ];
  return {
    provider: "aws",
    currency: "USD",
    period: "month",
    total: 1240,
    nodes,
    byCategory: [
      { category: "Compute", monthly: 576 },
      { category: "Messaging", monthly: 232 },
      { category: "Database", monthly: 184 },
      { category: "Networking", monthly: 102 },
      { category: "Cache", monthly: 98 },
      { category: "Observability", monthly: 48 },
    ],
    assumptions: COST_ASSUMPTIONS,
    evidenceIds: ["ev_cost_total", "ev_cost_postgres"],
    calculatedAt: FOOD_ANALYZED_AT,
    architectureVersion: 3,
  };
}

// --- Food Delivery: drift (spec §45) ----------------------------------------

export function foodDrift(): DriftReport {
  const items: DriftReport["items"] = [
    {
      id: "drift_api_replicas",
      nodeId: "api",
      subject: "API replicas",
      expected: "3",
      actual: "5",
      severity: "medium",
      status: "drifted",
    },
    {
      id: "drift_redis",
      nodeId: "redis",
      subject: "Redis",
      expected: "enabled",
      actual: "disabled",
      severity: "high",
      status: "drifted",
    },
    {
      id: "drift_db_replicas",
      nodeId: "postgres",
      subject: "DB replicas",
      expected: "2",
      actual: "1",
      severity: "high",
      status: "drifted",
    },
    {
      id: "drift_prometheus",
      nodeId: "prometheus",
      subject: "Prometheus",
      expected: "deployed",
      actual: "not found",
      severity: "medium",
      status: "missing",
    },
    {
      id: "drift_unmanaged_ec2",
      nodeId: null,
      subject: "EC2 instance i-0a1f93c2",
      expected: "not in architecture",
      actual: "t3.large, running",
      severity: "low",
      status: "unexpected",
    },
    {
      id: "drift_kafka_brokers",
      nodeId: "kafka",
      subject: "Kafka brokers",
      expected: "3",
      actual: "3",
      severity: "info",
      status: "matching",
    },
    {
      id: "drift_order_replicas",
      nodeId: "order_service",
      subject: "Order Service replicas",
      expected: "2",
      actual: "2",
      severity: "info",
      status: "matching",
    },
    {
      id: "drift_payment_replicas",
      nodeId: "payment_service",
      subject: "Payment Service replicas",
      expected: "2",
      actual: "2",
      severity: "info",
      status: "matching",
    },
  ];
  return {
    checkedAt: "2026-09-19T07:00:00.000Z",
    source: "aws",
    architectureVersion: 3,
    items,
    summary: driftSummary(items),
  };
}

export function driftSummary(items: DriftReport["items"]): DriftReport["summary"] {
  const matching = items.filter((i) => i.status === "matching").length;
  return { drifted: items.length - matching, matching };
}

// --- Food Delivery: evolution & migration (spec §42–43) ---------------------

export const FOOD_VERSION_METRICS: Record<string, MockVersionMetrics> = {
  "1": { maxDailyActiveUsers: 2_000_000, monthlyCost: 410 },
  "2": { maxDailyActiveUsers: 7_800_000, monthlyCost: 1192 },
  "3": { maxDailyActiveUsers: 7_800_000, monthlyCost: 1240 },
};

export function foodEvolution(): Evolution {
  return {
    stages: [
      {
        id: "stage_v1",
        label: "V1",
        dailyActiveUsers: 100_000,
        status: "past",
        architectureVersion: 1,
        trigger: "Launch in one metro area.",
        changes: [
          { kind: "add", description: "API with Order and Payment services" },
          { kind: "add", description: "PostgreSQL primary store" },
          { kind: "add", description: "Redis menu cache and CDN for images" },
        ],
        monthlyCost: 410,
        risk: "low",
        migrationId: null,
        maxSupportedDailyActiveUsers: 2_000_000,
      },
      {
        id: "stage_v2",
        label: "V2",
        dailyActiveUsers: 5_000_000,
        status: "current",
        architectureVersion: 3,
        trigger: "Expansion to 12 cities: dispatch became the slowest step at dinner peak.",
        changes: [
          { kind: "add", description: "Kafka order events" },
          { kind: "add", description: "Dispatch Worker off the request path" },
          { kind: "add", description: "Prometheus monitoring" },
        ],
        monthlyCost: 1240,
        risk: "medium",
        migrationId: null,
        maxSupportedDailyActiveUsers: 7_800_000,
      },
      {
        id: "stage_v3",
        label: "V3",
        dailyActiveUsers: 50_000_000,
        status: "planned",
        architectureVersion: null,
        trigger: "National rollout: PostgreSQL connections saturate at ~7.8M DAU.",
        changes: [
          { kind: "change", description: "PostgreSQL replicas 1 → 3 with automatic failover" },
          { kind: "add", description: "PgBouncer connection pooling" },
          { kind: "change", description: "Redis replicas 1 → 3 (cluster mode)" },
          { kind: "change", description: "CDN caches menu API responses" },
          { kind: "change", description: "API replicas 3 → 12" },
          { kind: "add", description: "Payment Queue buffers provider calls" },
        ],
        monthlyCost: 6900,
        risk: "high",
        migrationId: "mig_v2_v3",
        maxSupportedDailyActiveUsers: 60_000_000,
      },
    ],
  };
}

/** Planned V3 topology, derived from the current v3 architecture with ordinary commands. */
export function foodStageArchitectures(current: Architecture): Record<string, Architecture> {
  const planned = applyCommands(current, [
    { type: "CHANGE_REPLICAS", nodeId: "postgres", replicas: 3 },
    { type: "CHANGE_REPLICAS", nodeId: "redis", replicas: 3 },
    { type: "CHANGE_REPLICAS", nodeId: "api", replicas: 12 },
    { type: "UPDATE_CONFIGURATION", nodeId: "cdn", configuration: { cacheApiResponses: true } },
    {
      type: "ADD_COMPONENT",
      node: mockNode(
        "pgbouncer",
        "service",
        "PgBouncer",
        "PgBouncer",
        { x: 250, y: 680 },
        { replicas: 2, poolSize: 400 },
        { description: "Connection pooling", domain: "data" },
      ),
    },
    {
      type: "ADD_COMPONENT",
      node: mockNode(
        "payment_queue",
        "queue",
        "Payment Queue",
        "Amazon SQS",
        { x: 800, y: 450 },
        { visibilityTimeoutSeconds: 60 },
        { description: "Buffers provider calls", domain: "payments" },
      ),
    },
    { type: "REMOVE_CONNECTIONS", edgeIds: ["e_order_postgres", "e_payment_postgres"] },
    { type: "CONNECT_COMPONENTS", edge: mockEdge("e_order_pgbouncer", "order_service", "pgbouncer", "SQL") },
    {
      type: "CONNECT_COMPONENTS",
      edge: mockEdge("e_payment_pgbouncer", "payment_service", "pgbouncer", "SQL"),
    },
    { type: "CONNECT_COMPONENTS", edge: mockEdge("e_pgbouncer_postgres", "pgbouncer", "postgres", "SQL") },
    {
      type: "CONNECT_COMPONENTS",
      edge: mockEdge("e_payment_queue", "payment_service", "payment_queue", "SQS", {
        synchronous: false,
        critical: false,
      }),
    },
  ]);
  return { stage_v3: { ...planned, id: "arch_food_stage_v3", createdBy: "ai" } };
}

export function foodMigrations(): MigrationPlan[] {
  return [
    {
      id: "mig_v2_v3",
      title: "Scale to 50M DAU (V2 → V3)",
      fromStageId: "stage_v2",
      toStageId: "stage_v3",
      status: "draft",
      overallRisk: "high",
      estimatedDuration: "6 weeks",
      rollbackPlan:
        "Every step is reversible on its own. Keep the V2 API pool deployable until step 6 is done " +
        "and fail back by shifting load-balancer weights.",
      steps: [
        {
          id: "mig_v2_v3_s1",
          order: 1,
          title: "Add PgBouncer in front of PostgreSQL",
          description: "Deploy PgBouncer in transaction mode and point Order and Payment services at it.",
          dependsOn: [],
          risk: "low",
          rollback: "Point services back at the PostgreSQL endpoint; remove PgBouncer.",
          estimatedDuration: "3 days",
          nodeIds: ["order_service", "payment_service", "postgres"],
          status: "pending",
        },
        {
          id: "mig_v2_v3_s2",
          order: 2,
          title: "Add two PostgreSQL replicas with automatic failover",
          description: "Create a Multi-AZ cluster with two read replicas and test a controlled failover.",
          dependsOn: ["mig_v2_v3_s1"],
          risk: "medium",
          rollback: "Detach the replicas; the primary keeps serving all traffic.",
          estimatedDuration: "1 week",
          nodeIds: ["postgres"],
          status: "pending",
        },
        {
          id: "mig_v2_v3_s3",
          order: 3,
          title: "Route read queries to replicas",
          description: "Send menu and order-history reads to replicas behind a feature flag, 10% → 100%.",
          dependsOn: ["mig_v2_v3_s2"],
          risk: "medium",
          rollback: "Turn the read-routing flag off.",
          estimatedDuration: "1 week",
          nodeIds: ["order_service", "postgres"],
          status: "pending",
        },
        {
          id: "mig_v2_v3_s4",
          order: 4,
          title: "Move Redis to cluster mode with 3 replicas",
          description: "Create the cluster, dual-write sessions for 24 h, then switch reads.",
          dependsOn: [],
          risk: "medium",
          rollback: "Switch reads back to the single node, which keeps receiving writes until cut-over.",
          estimatedDuration: "4 days",
          nodeIds: ["redis", "api"],
          status: "pending",
        },
        {
          id: "mig_v2_v3_s5",
          order: 5,
          title: "Cache menu API responses at the CDN",
          description: "Add cache behaviours for GET /menus/* with a 60 s TTL and surrogate-key purges.",
          dependsOn: ["mig_v2_v3_s4"],
          risk: "low",
          rollback: "Remove the cache behaviours; the CDN forwards to the load balancer again.",
          estimatedDuration: "2 days",
          nodeIds: ["cdn", "api"],
          status: "pending",
        },
        {
          id: "mig_v2_v3_s6",
          order: 6,
          title: "Add the Payment Queue and scale the API to 12 replicas",
          description: "Buffer provider calls in SQS with retries, then raise API autoscaling limits to 12.",
          dependsOn: ["mig_v2_v3_s3", "mig_v2_v3_s5"],
          risk: "high",
          rollback:
            "Disable the queue consumer flag so payments call the provider synchronously; lower API limits.",
          estimatedDuration: "2 weeks",
          nodeIds: ["api", "payment_service", "payment_provider"],
          status: "pending",
        },
      ],
    },
  ];
}

// --- Food Delivery: simulation scenarios -------------------------------------

export function foodScenarios(current: Architecture): SimulationScenario[] {
  const regional = current.nodes
    .filter((n) => n.type !== "client" && n.type !== "external" && n.type !== "cdn")
    .map((n) => n.id);
  return [
    {
      id: "scn_pg_failure",
      kind: "database_failure",
      label: "PostgreSQL failure",
      description: "The PostgreSQL primary becomes unreachable for the whole run.",
      targetNodeIds: ["postgres"],
    },
    {
      id: "scn_redis_failure",
      kind: "redis_failure",
      label: "Redis failure",
      description: "The Redis node restarts with an empty cache.",
      targetNodeIds: ["redis"],
    },
    {
      id: "scn_kafka_failure",
      kind: "kafka_failure",
      label: "Kafka outage",
      description: "All three Kafka brokers stop accepting writes.",
      targetNodeIds: ["kafka"],
    },
    {
      id: "scn_traffic_spike",
      kind: "traffic_spike",
      label: "Dinner-rush traffic spike",
      description: "Traffic rises sharply at the load balancer, following the chosen traffic level.",
      targetNodeIds: ["lb"],
    },
    {
      id: "scn_region_failure",
      kind: "region_failure",
      label: "us-east-1 region failure",
      description: "Every component in us-east-1 becomes unavailable.",
      targetNodeIds: regional,
    },
    {
      id: "scn_payment_partition",
      kind: "network_partition",
      label: "Network partition: Payment Provider",
      description: "Payment Service cannot reach the Payment Provider.",
      targetNodeIds: ["payment_provider"],
    },
  ];
}

// --- URL Shortener (minimal) --------------------------------------------------

export const URL_VERSION_METRICS: Record<string, MockVersionMetrics> = {
  "1": { maxDailyActiveUsers: 6_000_000, monthlyCost: 310 },
};

export function urlReliability(): ReliabilityAnalysis {
  return {
    availability: { target: 0.999, estimated: 0.9993, monthlyDowntimeMinutes: 31 },
    entrypoints: [{ nodeId: "lb", availability: 0.9993 }],
    singlePointsOfFailure: [],
    criticalPaths: [{ nodeIds: ["client", "lb", "api", "postgres"], availability: 0.9993 }],
    cascadeRisks: [],
    criticalEdgeIds: ["e_client_lb", "e_lb_api", "e_api_redis", "e_api_postgres"],
    analyzedAt: URL_ANALYZED_AT,
    architectureVersion: 1,
  };
}

export function urlSecurity(): SecurityAnalysis {
  return {
    score: 95,
    trustBoundaries: [
      { id: "tb_internet", name: "Internet", nodeIds: ["client"] },
      { id: "tb_vpc", name: "VPC", nodeIds: ["lb", "api", "redis", "postgres"] },
    ],
    exposure: [
      { nodeId: "client", level: "public", reason: "Browsers and API clients." },
      { nodeId: "lb", level: "public", reason: "Internet-facing HTTPS listener." },
      { nodeId: "api", level: "internal", reason: "Reachable only from the load balancer." },
      { nodeId: "redis", level: "private", reason: "Private subnet." },
      { nodeId: "postgres", level: "private", reason: "Private subnet." },
    ],
    controls: [
      controls("api", { authentication: true, authorization: true, encryptionInTransit: true }),
      controls("postgres", { encryptionAtRest: true, encryptionInTransit: true, secretsManagement: true }),
      controls("redis", { encryptionInTransit: true }),
    ],
    threats: [
      {
        id: "thr_url_spam",
        title: "Bulk link creation for spam",
        category: "denial_of_service",
        severity: "low",
        nodeIds: ["lb", "api"],
        mitigation: "Rate limit POST /links per client IP.",
        evidenceId: null,
      },
    ],
    analyzedAt: URL_ANALYZED_AT,
    architectureVersion: 1,
  };
}

export function urlObservability(): ObservabilityAnalysis {
  const rows = [
    coverage("lb", "11011"),
    coverage("api", "11111"),
    coverage("redis", "10011"),
    coverage("postgres", "11011"),
  ];
  return {
    score: 94,
    coverage: rows,
    slos: [
      {
        id: "slo_redirect_latency",
        name: "Redirects under 100 ms (P99)",
        nodeIds: ["api", "redis"],
        target: 0.99,
        current: 0.996,
        errorBudgetRemaining: 0.88,
        window: "30d",
      },
    ],
    gaps: observabilityGaps(rows),
    analyzedAt: URL_ANALYZED_AT,
    architectureVersion: 1,
  };
}

export function urlCost(): CostEstimate {
  const nodes = [
    nodeCost("api", [["3 × container (1 vCPU, 2 GB)", 108]]),
    nodeCost("redis", [["2 × cache node (4 GB)", 74]]),
    nodeCost("postgres", [
      ["2 × db instance (2 vCPU, 8 GB)", 102],
      ["100 GB storage", 10],
    ]),
    nodeCost("lb", [["Load balancer", 16]]),
  ];
  return {
    provider: "aws",
    currency: "USD",
    period: "month",
    total: 310,
    nodes,
    byCategory: [
      { category: "Compute", monthly: 108 },
      { category: "Database", monthly: 112 },
      { category: "Cache", monthly: 74 },
      { category: "Networking", monthly: 16 },
    ],
    assumptions: [{ id: "C-001", statement: "On-demand eu-west-1 prices, 730 hours per month." }],
    evidenceIds: [],
    calculatedAt: URL_ANALYZED_AT,
    architectureVersion: 1,
  };
}

export function urlDrift(): DriftReport {
  const items: DriftReport["items"] = [
    {
      id: "drift_url_api_replicas",
      nodeId: "api",
      subject: "API replicas",
      expected: "3",
      actual: "3",
      severity: "info",
      status: "matching",
    },
    {
      id: "drift_url_redis_replicas",
      nodeId: "redis",
      subject: "Redis replicas",
      expected: "2",
      actual: "2",
      severity: "info",
      status: "matching",
    },
  ];
  return {
    checkedAt: "2026-08-21T07:00:00.000Z",
    source: "kubernetes",
    architectureVersion: 1,
    items,
    summary: driftSummary(items),
  };
}

export function urlEvolution(): Evolution {
  return {
    stages: [
      {
        id: "stage_url_v1",
        label: "V1",
        dailyActiveUsers: 500_000,
        status: "current",
        architectureVersion: 1,
        trigger: "Initial launch.",
        changes: [{ kind: "add", description: "API with Redis and PostgreSQL" }],
        monthlyCost: 310,
        risk: "low",
        migrationId: null,
        maxSupportedDailyActiveUsers: 6_000_000,
      },
      {
        id: "stage_url_v2",
        label: "V2",
        dailyActiveUsers: 10_000_000,
        status: "planned",
        architectureVersion: null,
        trigger: "API CPU reaches the warning threshold at ~6M DAU.",
        changes: [
          { kind: "change", description: "API replicas 3 → 8" },
          { kind: "change", description: "Redis replicas 2 → 3" },
        ],
        monthlyCost: 720,
        risk: "low",
        migrationId: null,
        maxSupportedDailyActiveUsers: 16_000_000,
      },
    ],
  };
}

export function urlStageArchitectures(current: Architecture): Record<string, Architecture> {
  const planned = applyCommands(current, [
    { type: "CHANGE_REPLICAS", nodeId: "api", replicas: 8 },
    { type: "CHANGE_REPLICAS", nodeId: "redis", replicas: 3 },
  ]);
  return { stage_url_v2: { ...planned, id: "arch_url_stage_v2", createdBy: "ai" } };
}

// --- Discovery (spec §44) ---------------------------------------------------

export const CONNECTORS: DiscoveryConnector[] = [
  {
    kind: "aws",
    label: "AWS",
    description: "Scan an AWS account with a read-only IAM role.",
    status: "connected",
    details: "Account 4821•••• · us-east-1",
  },
  {
    kind: "kubernetes",
    label: "Kubernetes",
    description: "Read deployments, services and ingresses from a cluster.",
    status: "connected",
    details: "Context food-prod-eks",
  },
  {
    kind: "terraform",
    label: "Terraform",
    description: "Parse Terraform state or plan files.",
    status: "not_connected",
    details: null,
  },
];

function resource(
  provider: DiscoveryConnectorKind,
  id: string,
  resourceType: string,
  name: string,
  mappedNodeType: DiscoveredResource["mappedNodeType"],
  region: string | null,
  status?: DiscoveredResource["status"],
): DiscoveredResource {
  return {
    id,
    provider,
    resourceType,
    name,
    region,
    mappedNodeType,
    status: status ?? (mappedNodeType ? "mapped" : "unmapped"),
  };
}

/** Bulk filler (IAM roles, log groups…) so the scan feels like a real account (spec §44: 183 resources). */
function filler(
  provider: DiscoveryConnectorKind,
  resourceType: string,
  prefix: string,
  count: number,
  status: DiscoveredResource["status"],
  region: string | null,
): DiscoveredResource[] {
  return Array.from({ length: count }, (_, i) =>
    resource(
      provider,
      `${resourceType}_${i + 1}`,
      resourceType,
      `${prefix}-${String(i + 1).padStart(2, "0")}`,
      null,
      region,
      status,
    ),
  );
}

function awsResources(): DiscoveredResource[] {
  const r = "us-east-1";
  return [
    resource("aws", "aws_cf_menu", "aws_cloudfront_distribution", "menu-assets-cdn", "cdn", null),
    resource("aws", "aws_alb_prod", "aws_lb", "food-prod-alb", "load_balancer", r),
    resource("aws", "aws_ecs_api", "aws_ecs_service", "api", "service", r),
    resource("aws", "aws_ecs_order", "aws_ecs_service", "order-service", "service", r),
    resource("aws", "aws_ecs_payment", "aws_ecs_service", "payment-service", "service", r),
    resource("aws", "aws_ecs_dispatch", "aws_ecs_service", "dispatch-worker", "worker", r),
    resource("aws", "aws_rds_food", "aws_rds_instance", "food-prod-postgres", "database", r),
    resource("aws", "aws_ec_menu", "aws_elasticache_cluster", "food-menu-cache", "cache", r),
    resource("aws", "aws_msk_orders", "aws_msk_cluster", "order-events", "queue", r),
    resource("aws", "aws_eks_prod", "aws_eks_cluster", "food-prod-eks", null, r, "ignored"),
    resource("aws", "aws_waf_edge", "aws_wafv2_web_acl", "edge-acl", null, null),
    ...filler("aws", "aws_iam_role", "food-role", 40, "ignored", null),
    ...filler("aws", "aws_security_group", "sg-food", 38, "ignored", r),
    ...filler("aws", "aws_cloudwatch_log_group", "/food/logs", 60, "ignored", r),
    ...filler("aws", "aws_secretsmanager_secret", "food-secret", 6, "ignored", r),
    ...filler("aws", "aws_route53_record", "food-dns", 10, "ignored", null),
    ...filler("aws", "aws_s3_bucket", "food-bucket", 12, "unmapped", r),
    ...filler("aws", "aws_nat_gateway", "food-nat", 3, "unmapped", r),
    ...filler("aws", "aws_lambda_function", "food-fn", 3, "unmapped", r),
  ];
}

function kubernetesResources(): DiscoveredResource[] {
  return [
    resource("kubernetes", "k8s_ingress", "Ingress", "food-ingress", "load_balancer", null),
    resource("kubernetes", "k8s_api", "Deployment", "api", "service", null),
    resource("kubernetes", "k8s_order", "Deployment", "order-service", "service", null),
    resource("kubernetes", "k8s_payment", "Deployment", "payment-service", "service", null),
    resource("kubernetes", "k8s_dispatch", "Deployment", "dispatch-worker", "worker", null),
    resource("kubernetes", "k8s_redis", "StatefulSet", "redis", "cache", null),
    resource("kubernetes", "k8s_prometheus", "StatefulSet", "prometheus", "observability", null),
    ...filler("kubernetes", "Service", "svc", 7, "ignored", null),
    ...filler("kubernetes", "ConfigMap", "config", 8, "ignored", null),
    ...filler("kubernetes", "HorizontalPodAutoscaler", "hpa", 2, "unmapped", null),
  ];
}

/**
 * What each connected source "discovers" in mock mode. AWS mirrors the Food Delivery
 * account as deployed (API at 5 replicas, no Prometheus: it runs in the cluster).
 */
export function discoverySnapshots(food: Architecture): Record<string, MockDiscoverySnapshot> {
  const awsNodes = new Set(food.nodes.filter((n) => n.id !== "prometheus").map((n) => n.id));
  const aws = applyCommands(
    {
      ...food,
      nodes: food.nodes.filter((n) => awsNodes.has(n.id)),
      edges: food.edges.filter((e) => awsNodes.has(e.source) && awsNodes.has(e.target)),
    },
    [{ type: "CHANGE_REPLICAS", nodeId: "api", replicas: 5 }],
  );
  const k8sNodes = new Set([
    "lb",
    "api",
    "order_service",
    "payment_service",
    "dispatch_worker",
    "redis",
    "prometheus",
  ]);
  const k8s: Architecture = {
    ...food,
    nodes: food.nodes
      .filter((n) => k8sNodes.has(n.id))
      .map((n) => (n.id === "lb" ? { ...n, name: "Ingress", technology: "NGINX Ingress" } : n)),
    edges: food.edges.filter((e) => k8sNodes.has(e.source) && k8sNodes.has(e.target)),
  };
  const discovered = (architecture: Architecture): Architecture => ({
    ...architecture,
    assumptions: [
      {
        id: "D-001",
        statement: "Mock discovery: mapped from fixture resources, not from a real scan.",
        source: "default",
      },
    ],
    createdBy: "discovery",
  });
  return {
    aws: { resources: awsResources(), architecture: discovered(aws) },
    kubernetes: { resources: kubernetesResources(), architecture: discovered(k8s) },
  };
}
