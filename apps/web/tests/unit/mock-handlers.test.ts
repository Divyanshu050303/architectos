import { afterAll, beforeEach, describe, expect, it, vi } from "vitest";

import {
  generateArchitecture,
  getArchitecture,
  listArchitectureVersions,
  saveArchitectureCommands,
} from "@/api/architectures";
import { getCapacity } from "@/api/capacity";
import { ApiError, setTransport } from "@/api/client";
import { getEvidence } from "@/api/evidence";
import { getJob } from "@/api/jobs";
import { resetMockDb } from "@/api/mock/db";
import { mockTransport } from "@/api/mock/transport";
import { listProjects } from "@/api/projects";
import { saveRequirements } from "@/api/requirements";
import { applyProposal, createProposal, fixFinding, rejectProposal } from "@/api/proposals";
import { getValidation, runValidation } from "@/api/validation";

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

describe("mock backend", () => {
  it("lists the seeded projects", async () => {
    const projects = await listProjects();
    expect(projects.map((p) => p.id).sort()).toEqual(["proj_food", "proj_large", "proj_pay", "proj_url"]);
    const food = projects.find((p) => p.id === "proj_food");
    expect(food?.architectureVersion).toBe(3);
    expect(food?.summary.dailyActiveUsers).toBe(2_400_000);
    expect(projects.find((p) => p.id === "proj_pay")?.summary.status).toBe("unknown");
  });

  it("returns null for a project without an architecture", async () => {
    await expect(getArchitecture("proj_pay")).resolves.toBeNull();
    const error = await captureError(generateArchitecture("proj_pay"));
    expect(error).toMatchObject({ status: 422, code: "requirements_missing" });
  });

  it("serves the spec's evidence record", async () => {
    const evidence = await getEvidence("ev_pg_connections");
    expect(evidence.claim).toBe("PostgreSQL is approaching connection capacity");
    expect(evidence.calculations.map((c) => c.value)).toEqual([438, 500, 350]);
    expect(evidence.assumptions.map((a) => a.id)).toEqual(["A-001", "A-007"]);
  });

  it("rejects commands against a stale version with 409 version_conflict", async () => {
    const error = await captureError(
      saveArchitectureCommands("proj_food", {
        baseVersion: 2,
        commands: [{ type: "RENAME_COMPONENT", nodeId: "api", name: "Gateway" }],
      }),
    );
    expect(error).toMatchObject({ status: 409, code: "version_conflict", details: { latestVersion: 3 } });
    expect(error.requestId).toBeTruthy();
  });

  it("applies commands as a new user version and clears analysis", async () => {
    const next = await saveArchitectureCommands("proj_food", {
      baseVersion: 3,
      commands: [{ type: "CHANGE_REPLICAS", nodeId: "postgres", replicas: 2 }],
    });
    expect(next.version).toBe(4);
    expect(next.createdBy).toBe("user");
    await expect(getCapacity("proj_food")).resolves.toBeNull();
    await expect(getValidation("proj_food")).resolves.toBeNull();

    const report = await runValidation("proj_food");
    expect(report.findings.some((f) => f.id === "f_spof_postgres")).toBe(false);
  });

  it("maps CommandError to 422 invalid_command", async () => {
    const error = await captureError(
      saveArchitectureCommands("proj_food", {
        baseVersion: 3,
        commands: [{ type: "RENAME_COMPONENT", nodeId: "ghost", name: "X" }],
      }),
    );
    expect(error).toMatchObject({ status: 422, code: "invalid_command" });
  });

  it("applying a proposal bumps the version as an AI change", async () => {
    const proposal = await createProposal("proj_food", {
      prompt: "Add Redis caching",
      baseVersion: 3,
      selectedNodeIds: [],
    });
    expect(proposal.kind).toBe("change");
    expect(proposal.changes.map((c) => c.op)).toEqual(["add_node", "add_edge"]);

    const architecture = await applyProposal(proposal.id, 3);
    expect(architecture.version).toBe(4);
    expect(architecture.createdBy).toBe("ai");
    expect(architecture.nodes).toHaveLength(13);

    const versions = await listArchitectureVersions("proj_food");
    expect(versions.at(-1)).toMatchObject({ version: 4, createdBy: "ai" });

    const again = await captureError(applyProposal(proposal.id, 4));
    expect(again.code).toBe("proposal_not_pending");
  });

  it("fixes a finding through a proposal and supports rejection", async () => {
    const fix = await fixFinding("proj_food", "f_spof_postgres");
    expect(fix.changes).toEqual([
      expect.objectContaining({
        op: "update_node",
        nodeId: "postgres",
        field: "replicas",
        before: 1,
        after: 2,
      }),
    ]);
    const rejected = await rejectProposal(fix.id);
    expect(rejected.status).toBe("rejected");
  });

  it("answers unknown prompts without changes", async () => {
    const proposal = await createProposal("proj_food", {
      prompt: "hello",
      baseVersion: 3,
      selectedNodeIds: [],
    });
    expect(proposal.kind).toBe("answer");
    expect(proposal.changes).toEqual([]);
  });

  it("generates an architecture through a job with derived step progress", async () => {
    await saveRequirements("proj_pay", {
      description: "Accept card payments and publish payment events for async settlement.",
      functional: ["Charge cards"],
      nonFunctional: {
        dailyActiveUsers: 100_000,
        peakRps: 500,
        availabilityTarget: 0.999,
        p99LatencyMs: 200,
        dataRetentionDays: null,
        regions: [],
      },
    });
    const start = Date.now();
    const clock = vi.spyOn(Date, "now").mockReturnValue(start);
    try {
      const job = await generateArchitecture("proj_pay");
      expect(job.status).toBe("running");
      expect(job.steps[0]?.status).toBe("running");

      clock.mockReturnValue(start + 1500);
      const midway = await getJob(job.id);
      expect(midway.steps.map((s) => s.status)).toEqual([
        "done",
        "done",
        "running",
        "pending",
        "pending",
        "pending",
      ]);

      clock.mockReturnValue(start + 10_000);
      const done = await getJob(job.id);
      expect(done).toMatchObject({ status: "succeeded", result: { architectureVersion: 1 } });
    } finally {
      clock.mockRestore();
    }
    const architecture = await getArchitecture("proj_pay");
    expect(architecture?.nodes.map((n) => n.type)).toContain("queue");
    await expect(getCapacity("proj_pay")).resolves.not.toBeNull();
  });
});
