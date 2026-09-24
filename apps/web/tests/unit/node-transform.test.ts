import { describe, expect, it } from "vitest";

import { type OverlayContext, toFlowNodes } from "@/features/architecture/utils/node-transform";
import type { ArchitectureNode } from "@/types/architecture";
import type { CapacityAnalysis, ComponentUtilization } from "@/types/capacity";
import type { CostEstimate } from "@/types/cost";
import type { ObservabilityAnalysis } from "@/types/observability";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis } from "@/types/security";
import type { Finding } from "@/types/validation";

const nodes: ArchitectureNode[] = [
  {
    id: "api",
    type: "service",
    name: "API",
    technology: "Node",
    configuration: {},
    position: { x: 0, y: 0 },
  },
  {
    id: "db",
    type: "database",
    name: "Postgres",
    technology: "PostgreSQL",
    configuration: {},
    position: { x: 0, y: 200 },
  },
];

function row(nodeId: string, resource: string, utilization: number, status: ComponentUtilization["status"]) {
  return {
    nodeId,
    resource,
    used: utilization * 100,
    limit: 100,
    unit: "",
    utilization,
    threshold: 0.7,
    status,
    evidenceId: null,
  };
}

function capacity(utilization: ComponentUtilization[]): CapacityAnalysis {
  return {
    projectId: "p",
    architectureVersion: 1,
    calculatedAt: "2026-01-01T00:00:00Z",
    load: { dailyActiveUsers: 1000, peakRps: 100, writesPerSecond: 10 },
    utilization,
    edges: [],
    bottleneck: null,
    envelope: { maxSupportedDailyActiveUsers: 2000, points: [] },
  };
}

function finding(overrides: Partial<Finding>): Finding {
  return {
    id: "f1",
    ruleId: "reliability.single_point_of_failure",
    category: "reliability",
    severity: "high",
    title: "Single point of failure",
    location: "Postgres",
    whyItMatters: "",
    recommendation: "",
    nodeIds: ["db"],
    edgeIds: [],
    evidenceIds: [],
    fixable: true,
    status: "open",
    ...overrides,
  };
}

function ctx(overrides: Partial<OverlayContext> = {}): OverlayContext {
  return {
    mode: "topology",
    capacity: null,
    findings: [],
    highlightedNodeIds: new Set(),
    selectedNodeIds: new Set(),
    ...overrides,
  };
}

const byId = (list: ReturnType<typeof toFlowNodes>, id: string) => list.find((n) => n.id === id)!.data;

describe("toFlowNodes", () => {
  it("marks nodes as not analyzed when there is no analysis data", () => {
    const [api] = toFlowNodes(nodes, ctx());
    expect(api?.data.status).toBe("default");
    expect(api?.data.statusLabel).toBe("Not analyzed");
    expect(api?.data.metric).toBeNull();
    expect(api?.data.category).toBe("Service");
    expect(api?.type).toBe("architecture");
  });

  it("uses the highest-utilisation backend row for status and metric", () => {
    const analysis = capacity([row("db", "cpu", 0.42, "healthy"), row("db", "connections", 0.82, "warning")]);
    const db = byId(toFlowNodes(nodes, ctx({ mode: "capacity", capacity: analysis })), "db");

    expect(db.status).toBe("warning");
    expect(db.statusLabel).toBe("Warning");
    expect(db.metric).toEqual({ label: "connections", value: "82%" });
    expect(db.utilization).toBe(0.82);
  });

  it("escalates to the worst of capacity status and open findings", () => {
    const analysis = capacity([row("db", "cpu", 0.3, "healthy")]);
    const withCritical = toFlowNodes(
      nodes,
      ctx({ capacity: analysis, findings: [finding({ severity: "critical" })] }),
    );
    expect(byId(withCritical, "db").statusLabel).toBe("Critical");

    const ignored = toFlowNodes(
      nodes,
      ctx({ capacity: analysis, findings: [finding({ severity: "critical", status: "ignored" })] }),
    );
    expect(byId(ignored, "db").statusLabel).toBe("Healthy");
  });

  it("maps high-severity findings to warning", () => {
    const db = byId(toFlowNodes(nodes, ctx({ findings: [finding({})] })), "db");
    expect(db.status).toBe("warning");
  });

  it("shows a SPOF badge only in reliability mode", () => {
    const findings = [finding({})];
    expect(byId(toFlowNodes(nodes, ctx({ mode: "reliability", findings })), "db").badges).toEqual([
      { label: "⚠ SPOF", tone: "warning" },
    ]);
    expect(byId(toFlowNodes(nodes, ctx({ mode: "topology", findings })), "db").badges).toEqual([]);
  });

  it("dims nodes outside an active highlight and carries selection", () => {
    const result = toFlowNodes(
      nodes,
      ctx({ highlightedNodeIds: new Set(["db"]), selectedNodeIds: new Set(["api"]) }),
    );
    expect(byId(result, "db")).toMatchObject({ highlighted: true, dimmed: false });
    expect(byId(result, "api")).toMatchObject({ highlighted: false, dimmed: true });
    expect(result.find((n) => n.id === "api")?.selected).toBe(true);
  });
});

const reliability: ReliabilityAnalysis = {
  availability: { target: 0.9995, estimated: 0.9981, monthlyDowntimeMinutes: 82 },
  entrypoints: [{ nodeId: "api", availability: 0.9982 }],
  singlePointsOfFailure: [
    {
      nodeId: "db",
      reason: "One instance",
      dependentNodeIds: ["api"],
      severity: "critical",
      evidenceId: null,
    },
  ],
  criticalPaths: [{ nodeIds: ["api", "db"], availability: 0.9981 }],
  cascadeRisks: [],
  criticalEdgeIds: [],
  analyzedAt: "2026-01-01T00:00:00Z",
  architectureVersion: 1,
};

const security: SecurityAnalysis = {
  score: 70,
  trustBoundaries: [],
  exposure: [
    { nodeId: "api", level: "public", reason: "Internet facing" },
    { nodeId: "db", level: "private", reason: "Private subnet" },
  ],
  controls: [],
  threats: [
    {
      id: "t1",
      title: "Weak auth",
      category: "spoofing",
      severity: "medium",
      nodeIds: ["api"],
      mitigation: "",
      evidenceId: null,
    },
    {
      id: "t2",
      title: "Injection",
      category: "tampering",
      severity: "critical",
      nodeIds: ["api"],
      mitigation: "",
      evidenceId: null,
    },
  ],
  analyzedAt: "2026-01-01T00:00:00Z",
  architectureVersion: 1,
};

const cost: CostEstimate = {
  provider: "aws",
  currency: "USD",
  period: "month",
  total: 1240,
  nodes: [{ nodeId: "db", monthly: 184, breakdown: [] }],
  byCategory: [],
  assumptions: [],
  evidenceIds: [],
  calculatedAt: "2026-01-01T00:00:00Z",
  architectureVersion: 1,
};

const observability: ObservabilityAnalysis = {
  score: 60,
  coverage: [
    { nodeId: "api", metrics: true, logs: true, traces: false, alerts: false, dashboards: true },
    { nodeId: "db", metrics: true, logs: true, traces: true, alerts: true, dashboards: true },
  ],
  slos: [],
  gaps: [{ nodeId: "api", missing: ["traces", "alerts"], recommendation: "Add tracing" }],
  analyzedAt: "2026-01-01T00:00:00Z",
  architectureVersion: 1,
};

describe("toFlowNodes analysis overlays", () => {
  it("reliability: SPOF badge, escalated status and availability metrics from the analysis", () => {
    const result = toFlowNodes(nodes, ctx({ mode: "reliability", reliability }));
    expect(byId(result, "db")).toMatchObject({
      badges: [{ label: "⚠ SPOF", tone: "danger" }],
      status: "critical",
      metric: { label: "path avail.", value: "99.81%" },
    });
    expect(byId(result, "api").metric).toEqual({ label: "avail.", value: "99.82%" });
  });

  it("security: exposure badge and the worst threat severity as status", () => {
    const result = toFlowNodes(nodes, ctx({ mode: "security", security }));
    expect(byId(result, "api")).toMatchObject({
      badges: [{ label: "Public", tone: "warning" }],
      status: "critical",
      statusLabel: "Critical threat",
      metric: { label: "threats", value: "2" },
    });
    expect(byId(result, "db")).toMatchObject({
      badges: [{ label: "Private", tone: "neutral" }],
      status: "healthy",
      statusLabel: "No threats",
      metric: null,
    });
  });

  it("security: not analyzed without an analysis", () => {
    const api = byId(toFlowNodes(nodes, ctx({ mode: "security" })), "api");
    expect(api).toMatchObject({ status: "default", statusLabel: "Not analyzed", badges: [] });
  });

  it("cost: formats the backend monthly figure per node", () => {
    const result = toFlowNodes(nodes, ctx({ mode: "cost", cost }));
    expect(byId(result, "db").metric).toEqual({ label: "", value: "$184/mo" });
    expect(byId(result, "api").metric).toBeNull();
  });

  it("observability: coverage chips with missing signals and status from gaps", () => {
    const result = toFlowNodes(nodes, ctx({ mode: "observability", observability }));
    const api = byId(result, "api");
    expect(api.coverage?.map((c) => `${c.key}:${c.present}`)).toEqual([
      "M:true",
      "L:true",
      "T:false",
      "A:false",
    ]);
    expect(api).toMatchObject({ status: "warning", statusLabel: "2 gaps" });
    expect(byId(result, "db")).toMatchObject({ status: "healthy", statusLabel: "Covered" });
    // Chips only in the observability lens.
    expect(byId(toFlowNodes(nodes, ctx({ observability })), "api").coverage).toBeNull();
  });

  it("simulation: maps each playback state to a status", () => {
    const states = new Map([
      ["db", "failed" as const],
      ["api", "impacted" as const],
    ]);
    const result = toFlowNodes(nodes, ctx({ mode: "simulation", simulation: states }));
    expect(byId(result, "db")).toMatchObject({
      simulation: "failed",
      status: "critical",
      statusLabel: "Failed",
    });
    expect(byId(result, "api")).toMatchObject({
      simulation: "impacted",
      status: "warning",
      statusLabel: "Impacted",
    });

    const cascade = toFlowNodes(
      nodes,
      ctx({ mode: "simulation", simulation: new Map([["api", "cascade" as const]]) }),
    );
    expect(byId(cascade, "api")).toMatchObject({ status: "critical", statusLabel: "Potential failure" });
    expect(byId(cascade, "db")).toMatchObject({ simulation: "normal", statusLabel: "Normal" });
  });
});
