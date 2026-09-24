import { describe, expect, it } from "vitest";

import { describeNode } from "@/features/architecture/hooks/useArchitectureCanvas";
import {
  disabledReason,
  LOADING_LABEL,
  type OverlayContext,
  SIMULATING_LABEL,
  toFlowNodes,
} from "@/features/architecture/utils/node-transform";
import type { SimulationNodeState } from "@/features/architecture/utils/simulation-playback";
import type { ArchitectureNode } from "@/types/architecture";
import type { Finding } from "@/types/validation";

function node(id: string, configuration: Record<string, unknown> = {}): ArchitectureNode {
  return {
    id,
    type: "service",
    name: id.toUpperCase(),
    technology: "Go",
    configuration,
    position: { x: 0, y: 0 },
  };
}

const EMPTY: ReadonlySet<string> = new Set();

function ctx(overrides: Partial<OverlayContext> = {}): OverlayContext {
  return {
    mode: "topology",
    capacity: null,
    findings: [] as Finding[],
    highlightedNodeIds: EMPTY,
    selectedNodeIds: EMPTY,
    ...overrides,
  };
}

function statusOf(nodes: ArchitectureNode[], context: OverlayContext) {
  return Object.fromEntries(
    toFlowNodes(nodes, context).map((n) => [n.id, { status: n.data.status, label: n.data.statusLabel }]),
  );
}

describe("node states (spec §25)", () => {
  it("marks components switched off in their configuration as disabled", () => {
    expect(disabledReason(node("a", { enabled: false }))).toBe("Disabled");
    expect(disabledReason(node("a", { replicas: 0 }))).toBe("Disabled · 0 replicas");
    expect(disabledReason(node("a", { replicas: 2, enabled: true }))).toBeNull();
    expect(disabledReason(node("a"))).toBeNull();

    const result = statusOf(
      [node("off", { enabled: false }), node("zero", { replicas: 0 }), node("on")],
      ctx(),
    );
    expect(result.off).toEqual({ status: "disabled", label: "Disabled" });
    expect(result.zero).toEqual({ status: "disabled", label: "Disabled · 0 replicas" });
    expect(result.on?.status).toBe("default");
  });

  it("disabled wins over analysis status", () => {
    const capacity = {
      projectId: "p",
      architectureVersion: 1,
      calculatedAt: "2026-01-01T00:00:00Z",
      load: { dailyActiveUsers: 1, peakRps: 1, writesPerSecond: 1 },
      utilization: [
        {
          nodeId: "off",
          resource: "CPU",
          used: 95,
          limit: 100,
          unit: "%",
          utilization: 0.95,
          threshold: 0.7,
          status: "critical" as const,
          evidenceId: null,
        },
      ],
      edges: [],
      bottleneck: null,
      evidenceIds: [],
    } as unknown as OverlayContext["capacity"];
    const result = statusOf([node("off", { enabled: false })], ctx({ mode: "capacity", capacity }));
    expect(result.off?.status).toBe("disabled");
  });

  it("shows loading while the overlay's analysis for this version is fetching", () => {
    const result = statusOf([node("a"), node("b", { enabled: false })], ctx({ loading: true }));
    expect(result.a).toEqual({ status: "loading", label: LOADING_LABEL });
    // A switched-off component stays disabled; loading is not an analysis result.
    expect(result.b?.status).toBe("disabled");
  });

  it("marks run components simulating only while playback is running", () => {
    const states = new Map<string, SimulationNodeState>([["failed", "failed"]]);
    const nodes = [node("failed"), node("pending"), node("outside")];
    const running = statusOf(
      nodes,
      ctx({ mode: "simulation", simulation: states, simulationActive: new Set(["failed", "pending"]) }),
    );
    expect(running.pending).toEqual({ status: "simulating", label: SIMULATING_LABEL });
    // A reached failure state stays visible.
    expect(running.failed).toEqual({ status: "critical", label: "Failed" });
    expect(running.outside?.status).toBe("default");

    const paused = statusOf(nodes, ctx({ mode: "simulation", simulation: states, simulationActive: null }));
    expect(paused.pending?.status).toBe("default");

    // Outside the simulation overlay nothing is simulating.
    const other = statusOf(nodes, ctx({ mode: "topology", simulationActive: new Set(["pending"]) }));
    expect(other.pending?.status).toBe("default");
  });

  it("exposes the state as accessible text", () => {
    const [flow] = toFlowNodes([node("a", { replicas: 0 })], ctx());
    if (!flow) throw new Error("no node");
    expect(describeNode({ ...flow.data, preview: null, direction: "LR" })).toContain(
      "status Disabled · 0 replicas",
    );
    const [loading] = toFlowNodes([node("b")], ctx({ loading: true }));
    if (!loading) throw new Error("no node");
    expect(describeNode({ ...loading.data, preview: null, direction: "LR" })).toContain(
      `status ${LOADING_LABEL}`,
    );
  });

  it("flags nodes changed by a new version", () => {
    const [changed, same] = toFlowNodes(
      [node("a"), node("b")],
      ctx({ versionChangedNodeIds: new Set(["a"]) }),
    );
    expect(changed?.data.versionChanged).toBe(true);
    expect(same?.data.versionChanged).toBe(false);
  });
});
