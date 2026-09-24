/**
 * MOCK FIXTURE DATA — DEV/TEST ONLY (NEXT_PUBLIC_API_MOCKS=true).
 *
 * "Marketplace (large)": a deterministic, generated architecture of ~120 components
 * across 9 domains, used to exercise the large-graph strategy (spec §64–65). The
 * capacity and validation figures are illustrative fixtures, not engine output.
 */
import type { Architecture, ArchitectureEdge, ArchitectureNode } from "@/types/architecture";
import type { CapacityAnalysis, ComponentUtilization } from "@/types/capacity";
import type { ComponentType } from "@/types/component";
import type { Finding, ValidationReport } from "@/types/validation";

import { mockEdge, mockNode, utilization } from "./builders";

export const LARGE_PROJECT_ID = "proj_large";
const CREATED_AT = "2026-09-12T12:00:00.000Z";
const ANALYZED_AT = "2026-09-12T12:05:00.000Z";

type DomainSpec = { id: string } & Partial<Record<ComponentType, string[]>>;

/** Order in which a domain's components are laid out in its grid block. */
const LAYOUT_ORDER: readonly ComponentType[] = [
  "client",
  "cdn",
  "load_balancer",
  "gateway",
  "service",
  "worker",
  "queue",
  "cache",
  "database",
  "storage",
  "observability",
  "external",
];

const TECHNOLOGY: Record<ComponentType, string> = {
  client: "Web / Mobile",
  cdn: "CloudFront",
  load_balancer: "AWS ALB",
  gateway: "Envoy",
  service: "Kotlin",
  worker: "Go",
  database: "PostgreSQL",
  cache: "Redis 7",
  queue: "Apache Kafka",
  storage: "Amazon S3",
  observability: "Grafana stack",
  external: "Third-party API",
};

const DOMAINS: DomainSpec[] = [
  {
    id: "edge",
    client: ["Web App", "Mobile App", "Seller Portal", "Admin Console"],
    cdn: ["CDN"],
    load_balancer: ["Load Balancer"],
    gateway: ["API Gateway", "GraphQL Gateway"],
    service: ["Web BFF", "Mobile BFF"],
  },
  {
    id: "identity",
    service: [
      "Auth Service",
      "User Profile Service",
      "Session Service",
      "Permissions Service",
      "MFA Service",
      "Org Service",
      "Audit Log Service",
    ],
    worker: ["Account Cleanup Worker"],
    database: ["Users DB"],
    cache: ["Session Cache"],
    queue: ["Identity Events"],
  },
  {
    id: "catalog",
    service: [
      "Catalog Service",
      "Pricing Service",
      "Inventory Service",
      "Media Service",
      "Reviews Service",
      "Recommendations Service",
      "Seller Listings Service",
      "Search API",
      "Query Suggest Service",
      "Ranking Service",
    ],
    worker: ["Price Sync Worker", "Image Resize Worker", "Search Indexer", "Reindex Worker"],
    database: ["Catalog DB", "Inventory DB", "Search Cluster"],
    cache: ["Catalog Cache", "Query Cache"],
    queue: ["Catalog Events"],
    storage: ["Media Bucket"],
  },
  {
    id: "ordering",
    service: [
      "Checkout Service",
      "Cart Service",
      "Order Service",
      "Promotions Service",
      "Tax Service",
      "Quote Service",
      "Subscription Service",
      "Order History Service",
      "Split Shipment Service",
    ],
    worker: ["Order Timeout Worker", "Invoice Worker"],
    database: ["Orders DB", "Carts DB"],
    cache: ["Cart Cache"],
    queue: ["Order Events"],
  },
  {
    id: "payments",
    service: [
      "Payment Service",
      "Fraud Service",
      "Refund Service",
      "Ledger Service",
      "Payout Service",
      "Wallet Service",
      "Currency Service",
    ],
    worker: ["Settlement Worker", "Chargeback Worker", "Reconciliation Worker"],
    database: ["Payments DB", "Ledger DB"],
    cache: ["Fraud Feature Cache"],
    queue: ["Payment Events"],
    external: ["Card Processor", "Bank Payout API"],
  },
  {
    id: "fulfillment",
    service: [
      "Shipping Service",
      "Warehouse Service",
      "Tracking Service",
      "Returns Service",
      "Carrier Gateway",
      "Delivery Slots Service",
      "Inventory Reservation Service",
      "Pickup Points Service",
    ],
    worker: ["Label Worker", "Tracking Poller", "Returns Worker"],
    database: ["Fulfillment DB"],
    cache: ["Tracking Cache"],
    queue: ["Shipment Events"],
    external: ["Carrier APIs"],
  },
  {
    id: "notifications",
    service: [
      "Notification Service",
      "Email Service",
      "SMS Service",
      "Push Service",
      "Preferences Service",
      "Templates Service",
      "Webhooks Service",
    ],
    worker: ["Email Sender", "SMS Sender", "Push Sender", "Digest Worker"],
    database: ["Notifications DB"],
    queue: ["Notification Queue"],
    external: ["Email Provider", "SMS Provider"],
  },
  {
    id: "platform",
    service: [
      "Events Collector",
      "Reporting API",
      "Experimentation Service",
      "Config Service",
      "Feature Flags",
      "Secrets Proxy",
    ],
    worker: ["ETL Worker", "Aggregation Worker", "Export Worker"],
    database: ["Analytics Warehouse"],
    storage: ["Data Lake"],
    queue: ["Clickstream"],
    observability: ["Prometheus", "Grafana", "Tracing", "Log Pipeline"],
  },
];

/** Databases deliberately left without a replica (they become SPOF findings). */
const SINGLE_INSTANCE_DATABASES = new Set(["payments_ledger_db", "notifications_notifications_db"]);

function slug(name: string): string {
  return name
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_|_$/g, "");
}

/** Deterministic pseudo-random in [0, 1) from a string (FNV-1a). */
function hash01(text: string): number {
  let h = 0x811c9dc5;
  for (let i = 0; i < text.length; i += 1) {
    h ^= text.charCodeAt(i);
    h = Math.imul(h, 0x01000193);
  }
  return (h >>> 0) / 0x1_0000_0000;
}

const COLUMNS = 3;
const BLOCK_WIDTH = 1300;
const BLOCK_HEIGHT = 1100;
const CELL_X = 260;
const CELL_Y = 150;
const CELLS_PER_ROW = 4;

interface DomainNodes {
  byType: Partial<Record<ComponentType, ArchitectureNode[]>>;
}

function configurationFor(type: ComponentType, id: string): Record<string, unknown> {
  switch (type) {
    case "service":
      return { replicas: 2 + Math.floor(hash01(id) * 5), cpu: "2 vCPU", memory: "4 GB" };
    case "worker":
      return { replicas: 1 + Math.floor(hash01(id) * 3), cpu: "1 vCPU", memory: "2 GB" };
    case "database":
      return {
        replicas: SINGLE_INSTANCE_DATABASES.has(id) ? 1 : 2,
        cpu: "8 vCPU",
        memory: "32 GB",
        maxConnections: 1000,
      };
    case "cache":
      return { replicas: 2, memory: "16 GB" };
    case "queue":
      return { brokers: 3, partitions: 24 };
    default:
      return {};
  }
}

function buildNodes(): { nodes: ArchitectureNode[]; domains: Map<string, DomainNodes> } {
  const nodes: ArchitectureNode[] = [];
  const domains = new Map<string, DomainNodes>();
  DOMAINS.forEach((domain, domainIndex) => {
    const originX = (domainIndex % COLUMNS) * BLOCK_WIDTH;
    const originY = Math.floor(domainIndex / COLUMNS) * BLOCK_HEIGHT;
    const byType: DomainNodes["byType"] = {};
    let cell = 0;
    for (const type of LAYOUT_ORDER) {
      for (const name of domain[type] ?? []) {
        const id = domain.id === "edge" ? slug(name) : `${domain.id}_${slug(name)}`;
        const node = mockNode(
          id,
          type,
          name,
          TECHNOLOGY[type],
          {
            x: originX + (cell % CELLS_PER_ROW) * CELL_X,
            y: originY + Math.floor(cell / CELLS_PER_ROW) * CELL_Y,
          },
          configurationFor(type, id),
          { domain: domain.id },
        );
        cell += 1;
        nodes.push(node);
        (byType[type] ??= []).push(node);
      }
    }
    domains.set(domain.id, { byType });
  });
  return { nodes, domains };
}

function buildEdges(domains: Map<string, DomainNodes>): ArchitectureEdge[] {
  const edges: ArchitectureEdge[] = [];
  const seen = new Set<string>();
  const connect = (
    source: ArchitectureNode | undefined,
    target: ArchitectureNode | undefined,
    protocol: string,
    options: Parameters<typeof mockEdge>[4] = {},
  ) => {
    if (!source || !target || source.id === target.id) return;
    const key = `${source.id}->${target.id}`;
    if (seen.has(key)) return;
    seen.add(key);
    edges.push(mockEdge(`e_${source.id}__${target.id}`, source.id, target.id, protocol, options));
  };
  const async = { synchronous: false, critical: false };
  const get = (domain: string, type: ComponentType, index = 0) => domains.get(domain)?.byType[type]?.[index];

  // Edge domain: clients → CDN / LB → gateways → BFFs.
  const edge = domains.get("edge")?.byType ?? {};
  for (const client of edge.client ?? []) {
    connect(client, edge.cdn?.[0], "HTTPS", { critical: false });
    connect(client, edge.load_balancer?.[0], "HTTPS");
  }
  for (const gateway of edge.gateway ?? []) connect(edge.load_balancer?.[0], gateway, "HTTP");
  for (const bff of edge.service ?? []) connect(edge.gateway?.[1], bff, "HTTP");
  const apiGateway = edge.gateway?.[0];

  for (const domain of DOMAINS) {
    if (domain.id === "edge") continue;
    const { byType } = domains.get(domain.id) ?? { byType: {} };
    const services = byType.service ?? [];
    const front = services[0];
    const databases = byType.database ?? [];
    const queue = byType.queue?.[0];

    if (domain.id !== "platform") {
      connect(apiGateway, front, "HTTP");
      for (const bff of edge.service ?? []) connect(bff, front, "gRPC", { critical: false });
    }
    services.forEach((service, i) => {
      if (i > 0) connect(front, service, "gRPC", { critical: i <= 2 });
      connect(service, databases[i % Math.max(databases.length, 1)], "SQL");
      if (i % 2 === 0)
        connect(service, byType.cache?.[i % Math.max(byType.cache?.length ?? 1, 1)], "RESP", {
          critical: false,
        });
    });
    connect(front, queue, "Kafka", async);
    for (const worker of byType.worker ?? []) {
      connect(queue, worker, "Kafka", async);
      connect(worker, databases[0], "SQL", { critical: false });
    }
    for (const external of byType.external ?? []) connect(services.at(-1), external, "HTTPS");
    for (const storage of byType.storage ?? []) connect(services[1], storage, "HTTPS", { critical: false });
  }

  // Cross-domain dependencies.
  connect(get("ordering", "service", 0), get("payments", "service", 0), "gRPC");
  connect(get("ordering", "service", 2), get("catalog", "service", 2), "gRPC");
  connect(get("ordering", "service", 0), get("identity", "service", 0), "gRPC");
  connect(get("payments", "service", 0), get("identity", "service", 0), "gRPC");
  connect(get("catalog", "service", 0), get("identity", "service", 0), "gRPC", { critical: false });
  connect(get("ordering", "queue"), get("fulfillment", "worker", 0), "Kafka", async);
  connect(get("ordering", "queue"), get("notifications", "worker", 0), "Kafka", async);
  connect(get("payments", "queue"), get("notifications", "worker", 0), "Kafka", async);
  connect(get("fulfillment", "queue"), get("notifications", "worker", 2), "Kafka", async);
  for (const domain of DOMAINS) {
    const front = get(domain.id, "service", 0);
    if (domain.id !== "platform")
      connect(get("platform", "observability", 0), front, "HTTP", { ...async, label: "scrape" });
    if (domain.id !== "platform" && domain.id !== "edge")
      connect(front, get("platform", "service", 0), "HTTP", async);
  }
  return edges;
}

export function largeArchitecture(): Architecture {
  const { nodes, domains } = buildNodes();
  return {
    id: "arch_large",
    projectId: LARGE_PROJECT_ID,
    version: 1,
    nodes,
    edges: buildEdges(domains),
    assumptions: [
      {
        id: "A-001",
        statement: "Generated demo architecture for large-graph views; not produced by the AI service.",
        source: "default",
      },
      { id: "A-002", statement: "Peak traffic is about 4× the daily average.", source: "user" },
    ],
    createdAt: CREATED_AT,
    createdBy: "ai",
  };
}

export function largeCapacity(architecture: Architecture): CapacityAnalysis {
  const rows: ComponentUtilization[] = [];
  for (const node of architecture.nodes) {
    const r = hash01(`${node.id}:load`);
    if (node.type === "service")
      rows.push(utilization(node.id, "cpu", Math.round(25 + r * 45), 100, "%", 0.75));
    if (node.type === "database") {
      const used = node.id === "ordering_orders_db" ? 820 : Math.round(250 + r * 400);
      rows.push(utilization(node.id, "connections", used, 1000, "connections", 0.7));
    }
    if (node.type === "cache")
      rows.push(utilization(node.id, "memory", Math.round((4 + r * 8) * 10) / 10, 16, "GB", 0.8));
    if (node.type === "queue")
      rows.push(utilization(node.id, "throughput", Math.round(20 + r * 40), 150, "MB/s", 0.75));
  }
  return {
    projectId: LARGE_PROJECT_ID,
    architectureVersion: 1,
    calculatedAt: ANALYZED_AT,
    load: { dailyActiveUsers: 20_000_000, peakRps: 180_000, writesPerSecond: 22_000 },
    utilization: rows,
    edges: architecture.edges.map((e) => ({
      edgeId: e.id,
      rps: Math.round(200 + hash01(`${e.id}:rps`) * 9_800),
    })),
    bottleneck: {
      nodeId: "ordering_orders_db",
      resource: "connections",
      description: "Orders DB connections reach the warning threshold first.",
      thresholdDailyActiveUsers: 26_000_000,
      evidenceId: null,
    },
    envelope: {
      maxSupportedDailyActiveUsers: 26_000_000,
      points: [
        { label: "1M", dailyActiveUsers: 1_000_000, status: "supported" },
        { label: "10M", dailyActiveUsers: 10_000_000, status: "supported" },
        { label: "20M", dailyActiveUsers: 20_000_000, status: "current" },
        { label: "26M", dailyActiveUsers: 26_000_000, status: "warning" },
        { label: "40M", dailyActiveUsers: 40_000_000, status: "exceeded" },
      ],
    },
  };
}

function finding(
  partial: Pick<Finding, "id" | "ruleId" | "category" | "severity" | "title" | "location" | "nodeIds"> &
    Partial<Finding>,
): Finding {
  return {
    whyItMatters: "Illustrative mock finding for the large demo architecture.",
    recommendation: "Review this component.",
    edgeIds: [],
    evidenceIds: [],
    fixable: false,
    status: "open",
    ...partial,
  };
}

export function largeValidation(): ValidationReport {
  const findings: Finding[] = [
    finding({
      id: "f_spof_payments_ledger_db",
      ruleId: "availability.single_point_of_failure",
      category: "reliability",
      severity: "critical",
      title: "Ledger DB is a single point of failure",
      location: "Ledger DB",
      nodeIds: ["payments_ledger_db"],
      whyItMatters: "Settlement and payouts stop when the single Ledger DB instance fails.",
      recommendation: "Run at least 2 replicas with automatic failover.",
      fixable: true,
    }),
    finding({
      id: "f_spof_notifications_notifications_db",
      ruleId: "availability.single_point_of_failure",
      category: "reliability",
      severity: "critical",
      title: "Notifications DB is a single point of failure",
      location: "Notifications DB",
      nodeIds: ["notifications_notifications_db"],
      whyItMatters: "Every notification channel depends on one instance.",
      recommendation: "Run at least 2 replicas with automatic failover.",
      fixable: true,
    }),
    finding({
      id: "f_large_checkout_timeout",
      ruleId: "resilience.missing_timeout",
      category: "reliability",
      severity: "high",
      title: "Missing timeout",
      location: "Checkout Service → Payment Service",
      nodeIds: ["ordering_checkout_service", "payments_payment_service"],
      edgeIds: ["e_ordering_checkout_service__payments_payment_service"],
      whyItMatters: "A slow payment call blocks checkout workers.",
      recommendation: "Add timeout <= configured SLA.",
      fixable: true,
    }),
    finding({
      id: "f_large_orders_db_connections",
      ruleId: "capacity.connection_saturation",
      category: "capacity",
      severity: "medium",
      title: "Orders DB connections above warning threshold",
      location: "Orders DB",
      nodeIds: ["ordering_orders_db"],
    }),
    finding({
      id: "f_large_no_tracing_fulfillment",
      ruleId: "observability.no_distributed_tracing",
      category: "observability",
      severity: "medium",
      title: "No distributed tracing in Fulfillment",
      location: "Fulfillment",
      nodeIds: [
        "fulfillment_shipping_service",
        "fulfillment_tracking_service",
        "fulfillment_carrier_gateway",
      ],
    }),
    finding({
      id: "f_large_webhooks_egress",
      ruleId: "security.unrestricted_egress",
      category: "security",
      severity: "medium",
      title: "Webhooks can call arbitrary hosts",
      location: "Webhooks Service",
      nodeIds: ["notifications_webhooks_service"],
    }),
    finding({
      id: "f_large_search_overprovisioned",
      ruleId: "cost.overprovisioned",
      category: "cost",
      severity: "low",
      title: "Search Cluster is over-provisioned",
      location: "Search Cluster",
      nodeIds: ["catalog_search_cluster"],
    }),
  ];
  const ids = (category: Finding["category"]) =>
    findings.filter((f) => f.category === category).map((f) => f.id);
  return {
    projectId: LARGE_PROJECT_ID,
    architectureVersion: 1,
    validatedAt: ANALYZED_AT,
    findings,
    health: {
      overall: 78,
      categories: [
        { category: "capacity", score: 86, findingIds: ids("capacity"), summary: "Headroom to ~26M DAU." },
        {
          category: "reliability",
          score: 64,
          findingIds: ids("reliability"),
          summary: "Two single-instance databases.",
        },
        {
          category: "security",
          score: 85,
          findingIds: ids("security"),
          summary: "Unrestricted webhook egress.",
        },
        {
          category: "observability",
          score: 74,
          findingIds: ids("observability"),
          summary: "Tracing gaps in Fulfillment.",
        },
        { category: "cost", score: 82, findingIds: ids("cost"), summary: "Search is sized above demand." },
      ],
    },
  };
}
