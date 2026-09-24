import { describe, expect, it } from "vitest";

import {
  type EdgeOverlayContext,
  formatCompact,
  toFlowEdges,
} from "@/features/architecture/utils/edge-transform";
import type { ArchitectureEdge } from "@/types/architecture";
import type { CapacityAnalysis } from "@/types/capacity";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis } from "@/types/security";

const edges: ArchitectureEdge[] = [
  { id: "e1", source: "api", target: "db", protocol: "TCP", synchronous: true, critical: true },
  { id: "e2", source: "api", target: "queue", synchronous: false, critical: false },
  { id: "e3", source: "worker", target: "queue", synchronous: true, critical: false },
];

const capacity: CapacityAnalysis = {
  projectId: "p",
  architectureVersion: 1,
  calculatedAt: "2026-01-01T00:00:00Z",
  load: { dailyActiveUsers: 1, peakRps: 1, writesPerSecond: 1 },
  utilization: [],
  edges: [{ edgeId: "e1", rps: 8200 }],
  bottleneck: null,
  envelope: { maxSupportedDailyActiveUsers: 1, points: [] },
};

function ctx(overrides: Partial<EdgeOverlayContext> = {}): EdgeOverlayContext {
  return { mode: "topology", capacity: null, highlightedNodeIds: new Set(), ...overrides };
}

const data = (list: ReturnType<typeof toFlowEdges>, id: string) => list.find((e) => e.id === id)!.data!;

describe("formatCompact", () => {
  it.each([
    [950, "950"],
    [8200, "8.2K"],
    [12000, "12K"],
    [1_500_000, "1.5M"],
    [999.8, "1K"],
  ])("formats %d as %s", (value, expected) => {
    expect(formatCompact(value)).toBe(expected);
  });
});

describe("toFlowEdges", () => {
  it("uses the domain label or protocol in topology mode", () => {
    const result = toFlowEdges(edges, ctx());
    expect(data(result, "e1")).toMatchObject({ label: "TCP", emphasis: "normal" });
    expect(data(result, "e2").label).toBeNull();
    expect(result[0]).toMatchObject({ source: "api", target: "db", type: "architecture" });
  });

  it("labels edges with backend throughput in capacity mode", () => {
    const result = toFlowEdges(edges, ctx({ mode: "capacity", capacity }));
    expect(data(result, "e1").label).toBe("8.2K RPS");
  });

  it("emphasises critical synchronous dependencies in reliability mode", () => {
    const result = toFlowEdges(edges, ctx({ mode: "reliability" }));
    expect(data(result, "e1")).toMatchObject({ label: "Critical dependency", emphasis: "critical" });
    expect(data(result, "e2").emphasis).toBe("normal");
    expect(data(result, "e3").emphasis).toBe("normal");
  });

  it("dims edges that do not touch a highlighted node", () => {
    const result = toFlowEdges(edges, ctx({ highlightedNodeIds: new Set(["db"]) }));
    expect(data(result, "e1").emphasis).toBe("normal");
    expect(data(result, "e2").emphasis).toBe("dimmed");
  });
});

describe("toFlowEdges analysis overlays", () => {
  it("reliability: uses the analysis' critical and cascade edges when present", () => {
    const reliability: ReliabilityAnalysis = {
      availability: { target: null, estimated: 0.999, monthlyDowntimeMinutes: 40 },
      entrypoints: [],
      singlePointsOfFailure: [],
      criticalPaths: [],
      cascadeRisks: [{ edgeId: "e3", reasons: ["retries"] }],
      criticalEdgeIds: ["e2"],
      analyzedAt: "2026-01-01T00:00:00Z",
      architectureVersion: 1,
    };
    const result = toFlowEdges(edges, ctx({ mode: "reliability", reliability }));
    expect(data(result, "e2")).toMatchObject({ label: "Critical dependency", emphasis: "critical" });
    // e1 is critical in the IR, but the analysis wins.
    expect(data(result, "e1").emphasis).toBe("normal");
    expect(data(result, "e3").label).toBe("Cascade risk");
  });

  it("security: labels connections that cross a trust boundary", () => {
    const security: SecurityAnalysis = {
      score: 80,
      trustBoundaries: [
        { id: "app", name: "App subnet", nodeIds: ["api", "worker"] },
        { id: "data", name: "Data subnet", nodeIds: ["db", "queue"] },
      ],
      exposure: [],
      controls: [],
      threats: [],
      analyzedAt: "2026-01-01T00:00:00Z",
      architectureVersion: 1,
    };
    const result = toFlowEdges(edges, ctx({ mode: "security", security }));
    expect(data(result, "e1")).toMatchObject({ label: "→ Data subnet", emphasis: "boundary" });
    expect(data(result, "e3")).toMatchObject({ emphasis: "boundary" });
    const same = toFlowEdges([{ ...edges[0]!, target: "worker" }], ctx({ mode: "security", security }));
    expect(same[0]!.data!.emphasis).toBe("normal");
  });

  it("simulation: marks connections between affected components", () => {
    const simulation = new Map([
      ["api", "impacted" as const],
      ["db", "failed" as const],
    ]);
    const result = toFlowEdges(edges, ctx({ mode: "simulation", simulation }));
    expect(data(result, "e1").emphasis).toBe("propagation");
    expect(data(result, "e2").emphasis).toBe("normal");
  });
});
