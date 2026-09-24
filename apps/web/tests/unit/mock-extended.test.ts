import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  compareVersions,
  getArchitecture,
  listArchitectureVersions,
  saveArchitectureCommands,
} from "@/api/architectures";
import { getCapacity } from "@/api/capacity";
import { ApiError, setTransport } from "@/api/client";
import { calculateCost, getCost } from "@/api/cost";
import { getDiscoveryRun, listConnectors, saveDiscovery, startDiscovery } from "@/api/discovery";
import { checkDrift, getDrift } from "@/api/drift";
import { listEvidence } from "@/api/evidence";
import { compareStages, getEvolution } from "@/api/evolution";
import { getMigration, listMigrations } from "@/api/migrations";
import { resetMockDb } from "@/api/mock/db";
import { mockTransport } from "@/api/mock/transport";
import { getObservability } from "@/api/observability";
import { applyProposal, createProposal } from "@/api/proposals";
import { getReliability, runReliabilityAnalysis } from "@/api/reliability";
import { getSecurity } from "@/api/security";
import {
  getLatestSimulation,
  getSimulationRun,
  listSimulationScenarios,
  startSimulation,
} from "@/api/simulations";

beforeEach(() => {
  resetMockDb();
  setTransport(mockTransport);
});

afterAll(() => setTransport(null));

async function captureError(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    if (error instanceof ApiError) return error;
    throw error;
  }
  throw new Error("expected the request to fail");
}

/** Freeze Date.now so step-based runs can be advanced deterministically. */
function useClock() {
  const start = Date.now();
  const clock = vi.spyOn(Date, "now").mockReturnValue(start);
  return {
    advance: (ms: number) => clock.mockReturnValue(start + ms),
    restore: () => clock.mockRestore(),
  };
}

describe("mock backend — extended surfaces", () => {
  it("serves the Food Delivery analysis fixtures", async () => {
    const [reliability, security, observability, cost] = await Promise.all([
      getReliability("proj_food"),
      getSecurity("proj_food"),
      getObservability("proj_food"),
      getCost("proj_food"),
    ]);
    expect(reliability?.singlePointsOfFailure.map((s) => s.nodeId)).toContain("postgres");
    expect(reliability?.cascadeRisks.map((c) => c.edgeId)).toContain("e_api_payment");
    expect(security?.exposure.find((e) => e.nodeId === "payment_provider")?.level).toBe("public");
    expect(observability?.gaps.length).toBeGreaterThan(0);
    expect(cost?.total).toBe(1240);
    expect(cost?.nodes.find((n) => n.nodeId === "postgres")?.monthly).toBe(184);
    expect(cost?.nodes.reduce((sum, n) => sum + n.monthly, 0)).toBe(1240);
    expect(cost?.byCategory.reduce((sum, c) => sum + c.monthly, 0)).toBe(1240);

    const latest = await getLatestSimulation("proj_food");
    expect(latest).toMatchObject({
      id: "sim_food_pg_failure",
      status: "succeeded",
      scenarioId: "scn_pg_failure",
    });
    expect(latest?.result?.metrics[0]).toEqual({
      metric: "P99 latency",
      before: 320,
      after: 1800,
      unit: "ms",
    });
  });

  it("returns null analyses for a project without an architecture", async () => {
    await expect(getReliability("proj_pay")).resolves.toBeNull();
    await expect(getCost("proj_pay")).resolves.toBeNull();
    await expect(getDrift("proj_pay")).resolves.toBeNull();
    await expect(getEvolution("proj_pay")).resolves.toBeNull();
    await expect(getLatestSimulation("proj_pay")).resolves.toBeNull();
    await expect(listSimulationScenarios("proj_pay")).resolves.toEqual([]);
    await expect(listMigrations("proj_pay")).resolves.toEqual([]);
    await expect(listEvidence("proj_pay")).resolves.toEqual([]);
  });

  it("drops cached analyses when the architecture changes and re-derives them on demand", async () => {
    await saveArchitectureCommands("proj_food", {
      baseVersion: 3,
      commands: [{ type: "CHANGE_REPLICAS", nodeId: "postgres", replicas: 2 }],
    });
    await expect(getReliability("proj_food")).resolves.toBeNull();
    await expect(getCost("proj_food")).resolves.toBeNull();
    await expect(getDrift("proj_food")).resolves.toBeNull();
    await expect(getLatestSimulation("proj_food")).resolves.toBeNull();

    const reliability = await runReliabilityAnalysis("proj_food");
    expect(reliability.architectureVersion).toBe(4);
    expect(reliability.singlePointsOfFailure.map((s) => s.nodeId)).not.toContain("postgres");

    const cost = await calculateCost("proj_food");
    expect(cost.nodes.find((n) => n.nodeId === "postgres")?.monthly).toBe(368);
    expect(cost.total).toBe(1240 + 184);
  });

  it("runs a simulation to a result whose affected nodes include hard dependents", async () => {
    const scenarios = await listSimulationScenarios("proj_food");
    expect(scenarios.map((s) => s.kind).sort()).toEqual([
      "database_failure",
      "kafka_failure",
      "network_partition",
      "redis_failure",
      "region_failure",
      "traffic_spike",
    ]);

    const clock = useClock();
    try {
      const run = await startSimulation("proj_food", {
        scenarioId: "scn_pg_failure",
        traffic: "current",
        durationMinutes: 5,
        environment: "production_like",
      });
      expect(run.status).toBe("running");
      expect(run.result).toBeNull();

      clock.advance(1000);
      const midway = await getSimulationRun(run.id);
      expect(midway.steps.map((s) => s.status)).toEqual(["done", "running", "pending", "pending", "pending"]);

      clock.advance(10_000);
      const done = await getSimulationRun(run.id);
      expect(done.status).toBe("succeeded");
      expect(done.result?.affectedNodeIds).toEqual(
        expect.arrayContaining(["postgres", "order_service", "payment_service", "api"]),
      );
      expect(done.result?.affectedNodeIds).not.toContain("client");
      expect(done.result?.cascadingFailure).toBe("potential");
      expect(done.result?.errorRate).toEqual({ before: 0.002, after: 0.124 });
      expect(done.result?.timeline[0]).toMatchObject({
        phase: "failure",
        nodeIds: ["postgres"],
        atSeconds: 0,
      });

      const latest = await getLatestSimulation("proj_food");
      expect(latest?.id).toBe(run.id);
    } finally {
      clock.restore();
    }
  });

  it("rejects an unknown scenario", async () => {
    const error = await captureError(
      startSimulation("proj_food", {
        scenarioId: "scn_nope",
        traffic: "current",
        durationMinutes: 5,
        environment: "staging",
      }),
    );
    expect(error).toMatchObject({ status: 422, code: "scenario_not_found" });
  });

  it("compares versions: applying the Redis proposal shows an added cache", async () => {
    const proposal = await createProposal("proj_food", {
      prompt: "Add Redis caching",
      baseVersion: 3,
      selectedNodeIds: [],
    });
    await applyProposal(proposal.id, 3);

    const comparison = await compareVersions("proj_food", 3, 4);
    expect(comparison.from).toEqual({ label: "v3", version: 3 });
    expect(comparison.components).toEqual([
      expect.objectContaining({ change: "added", type: "cache", name: "Redis Read Cache" }),
    ]);
    expect(comparison.connections).toEqual([
      expect.objectContaining({ change: "added", sourceName: "API", targetName: "Redis Read Cache" }),
    ]);
    expect(comparison.cost).toEqual({ before: 1240, after: null, currency: "USD" });

    const history = await compareVersions("proj_food", 1, 3);
    expect(
      history.components
        .filter((c) => c.change === "added")
        .map((c) => c.nodeId)
        .sort(),
    ).toEqual(["dispatch_worker", "kafka", "prometheus"]);
    expect(history.capacity).toEqual({
      beforeMaxDailyActiveUsers: 2_000_000,
      afterMaxDailyActiveUsers: 7_800_000,
    });
  });

  it("serves the evolution roadmap, stage comparison and migration plan", async () => {
    const evolution = await getEvolution("proj_food");
    expect(evolution?.stages.map((s) => [s.label, s.dailyActiveUsers, s.status])).toEqual([
      ["V1", 100_000, "past"],
      ["V2", 5_000_000, "current"],
      ["V3", 50_000_000, "planned"],
    ]);

    const comparison = await compareStages("proj_food", "stage_v2", "stage_v3");
    expect(comparison.components).toEqual(
      expect.arrayContaining([
        expect.objectContaining({ change: "added", nodeId: "pgbouncer" }),
        expect.objectContaining({
          change: "changed",
          nodeId: "postgres",
          details: [{ field: "replicas", before: 1, after: 3 }],
        }),
      ]),
    );
    expect(comparison.cost).toEqual({ before: 1240, after: 6900, currency: "USD" });

    const [plan] = await listMigrations("proj_food");
    expect(plan?.id).toBe("mig_v2_v3");
    const migration = await getMigration("mig_v2_v3");
    expect(migration.steps).toHaveLength(6);
    const order = new Map(migration.steps.map((s) => [s.id, s.order]));
    for (const step of migration.steps) {
      for (const dependency of step.dependsOn) expect(order.get(dependency)).toBeLessThan(step.order);
    }
  });

  it("serves the drift fixture and re-checks it", async () => {
    const drift = await getDrift("proj_food");
    expect(drift?.items.find((i) => i.subject === "API replicas")).toMatchObject({
      expected: "3",
      actual: "5",
      status: "drifted",
    });
    expect(drift?.items.find((i) => i.subject === "Redis")).toMatchObject({
      expected: "enabled",
      actual: "disabled",
    });
    expect(drift?.items.find((i) => i.subject === "DB replicas")).toMatchObject({
      expected: "2",
      actual: "1",
    });
    expect(drift?.summary).toEqual({ drifted: 5, matching: 3 });

    const checked = await checkDrift("proj_food");
    expect(checked.summary).toEqual(drift?.summary);
    const error = await captureError(checkDrift("proj_large"));
    expect(error).toMatchObject({ status: 422, code: "discovery_required" });
  });

  it("discovers AWS resources and saves them as a discovery version", async () => {
    const connectors = await listConnectors();
    expect(connectors.map((c) => [c.kind, c.status])).toEqual([
      ["aws", "connected"],
      ["kubernetes", "connected"],
      ["terraform", "not_connected"],
    ]);
    const refused = await captureError(startDiscovery("proj_food", { connector: "terraform", options: {} }));
    expect(refused).toMatchObject({ status: 422, code: "connector_not_connected" });

    const clock = useClock();
    try {
      const run = await startDiscovery("proj_food", { connector: "aws", options: { region: "us-east-1" } });
      expect(run.steps.map((s) => s.label)).toEqual([
        "Connect",
        "Discover",
        "Normalize",
        "Generate architecture",
        "Review",
      ]);
      expect(run.resources).toEqual([]);
      const early = await captureError(saveDiscovery(run.id, 3));
      expect(early).toMatchObject({ status: 409, code: "discovery_not_ready" });

      clock.advance(10_000);
      const done = await getDiscoveryRun(run.id);
      expect(done.status).toBe("succeeded");
      expect(done.summary.total).toBe(183);
      expect(done.resources.find((r) => r.resourceType === "aws_rds_instance")?.mappedNodeType).toBe(
        "database",
      );
      expect(done.proposedArchitecture?.version).toBe(4);

      const stale = await captureError(saveDiscovery(run.id, 2));
      expect(stale).toMatchObject({ status: 409, code: "version_conflict" });

      const saved = await saveDiscovery(run.id, 3);
      expect(saved).toMatchObject({ version: 4, createdBy: "discovery" });
      expect(saved.nodes.find((n) => n.id === "api")?.configuration.replicas).toBe(5);
    } finally {
      clock.restore();
    }

    const versions = await listArchitectureVersions("proj_food");
    expect(versions.at(-1)).toMatchObject({ version: 4, createdBy: "discovery" });
    await expect(getCapacity("proj_food")).resolves.toBeNull();
  });

  it("seeds a large generated project for the large-graph strategy", async () => {
    const architecture = await getArchitecture("proj_large");
    expect(architecture?.nodes.length).toBeGreaterThanOrEqual(100);
    const ids = new Set(architecture?.nodes.map((n) => n.id));
    expect(ids.size).toBe(architecture?.nodes.length);
    expect(architecture?.edges.every((e) => ids.has(e.source) && ids.has(e.target))).toBe(true);
    const domains = new Set(architecture?.nodes.map((n) => n.domain));
    expect(domains.has(undefined)).toBe(false);
    expect(domains.size).toBeGreaterThanOrEqual(8);
    await expect(getCapacity("proj_large")).resolves.not.toBeNull();
    const scenarios = await listSimulationScenarios("proj_large");
    expect(scenarios.length).toBeGreaterThan(0);
  });

  it("gives every Food Delivery node a domain and lists cited evidence", async () => {
    const architecture = await getArchitecture("proj_food");
    expect(architecture?.nodes.every((n) => Boolean(n.domain))).toBe(true);
    const evidence = await listEvidence("proj_food");
    expect(evidence.map((e) => e.id)).toEqual(
      expect.arrayContaining(["ev_pg_connections", "ev_pg_spof", "ev_cost_postgres"]),
    );
  });
});
