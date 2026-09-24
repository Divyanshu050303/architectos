import { afterAll, beforeEach, describe, expect, it } from "vitest";

import { ApiError, setTransport } from "@/api/client";
import { resetMockDb } from "@/api/mock/db";
import { chunkText, handleMockStreamRequest, mockProposalEvents } from "@/api/mock/proposal-stream";
import { mockTransport } from "@/api/mock/transport";
import { fixFinding, type ProposalStep, streamProposal } from "@/api/proposals";
import { ProposalStreamEventSchema } from "@/schemas/proposals";

beforeEach(() => {
  resetMockDb();
  setTransport(mockTransport);
});

afterAll(() => setTransport(null));

const input = (prompt: string, baseVersion = 3) => ({ prompt, baseVersion, selectedNodeIds: [] });

async function readAll(stream: ReadableStream<Uint8Array>): Promise<string> {
  const reader = stream.getReader();
  const decoder = new TextDecoder();
  let text = "";
  for (;;) {
    const { done, value } = await reader.read();
    if (done) return text + decoder.decode();
    text += decoder.decode(value, { stream: true });
  }
}

describe("mock proposal stream (fixture data)", () => {
  it("streams schema-valid NDJSON: steps, explanation text, then the proposal last", async () => {
    const result = handleMockStreamRequest(
      { method: "POST", path: "/projects/proj_food/proposals/stream", body: input("Add Redis caching") },
      "req_1",
    );
    expect(result?.kind).toBe("stream");
    if (result?.kind !== "stream") return;

    const lines = (await readAll(result.stream)).trim().split("\n");
    const events = lines.map((line) => ProposalStreamEventSchema.parse(JSON.parse(line)));
    expect(events.at(-1)?.type).toBe("proposal");
    expect(events.filter((e) => e.type === "proposal")).toHaveLength(1);

    const text = events.flatMap((e) => (e.type === "text" ? [e.delta] : [])).join("");
    expect(text).toMatch(/^Introduce Redis\. PostgreSQL read utilization/);

    const firstRunning = events.findIndex((e) => e.type === "progress" && e.step.status === "running");
    const pending = events.slice(0, firstRunning);
    expect(pending.map((e) => (e.type === "progress" ? e.step.label : null))).toEqual([
      "Understanding request",
      "Drafting proposal",
      "Validating proposal",
      "Checking impact",
    ]);
    // Text is only streamed while drafting; the diff only exists in the final event.
    const draftRunning = events.findIndex(
      (e) => e.type === "progress" && e.step.id === "draft" && e.step.status === "running",
    );
    const draftDone = events.findIndex(
      (e) => e.type === "progress" && e.step.id === "draft" && e.step.status === "done",
    );
    events.forEach((e, i) => {
      if (e.type === "text") expect(i > draftRunning && i < draftDone).toBe(true);
    });
  });

  it("answers the stream route only for POST", () => {
    expect(
      handleMockStreamRequest(
        { method: "GET", path: "/projects/proj_food/proposals/stream", body: null },
        "r",
      ),
    ).toBeNull();
    expect(
      handleMockStreamRequest({ method: "POST", path: "/projects/proj_food", body: null }, "r"),
    ).toBeNull();
  });

  it("rejects before streaming with the JSON error envelope", async () => {
    const conflict = await streamProposal("proj_food", input("Add Redis caching", 1)).catch(
      (e: unknown) => e,
    );
    expect(conflict).toBeInstanceOf(ApiError);
    expect(conflict).toMatchObject({ status: 409, code: "version_conflict" });

    const missing = await streamProposal("proj_nope", input("Add Redis caching")).catch((e: unknown) => e);
    expect(missing).toMatchObject({ status: 404, code: "project_not_found" });
  });

  it("works end to end through the mock transport with a mock validation", async () => {
    const steps: ProposalStep[] = [];
    let text = "";
    const proposal = await streamProposal("proj_food", input("Add Redis caching"), {
      onProgress: (step) => steps.push(step),
      onText: (delta) => {
        text += delta;
      },
    });
    expect(proposal.kind).toBe("change");
    expect(proposal.validation).toMatchObject({ passes: true, newFindings: [] });
    expect(proposal.validation?.summary).toMatch(/^Mock validation/);
    expect(text).toContain("Introduce Redis.");
    expect(steps.at(-1)).toEqual({ id: "impact", label: "Checking impact", status: "done" });
  });

  it("stops producing events when the consumer aborts", async () => {
    const controller = new AbortController();
    let texts = 0;
    const error = await streamProposal("proj_food", input("Add Redis caching"), {
      signal: controller.signal,
      onText: () => {
        texts += 1;
        controller.abort(new DOMException("Stopped", "AbortError"));
      },
    }).catch((e: unknown) => e);
    expect(error).toMatchObject({ name: "AbortError" });
    expect(texts).toBe(1);
  });

  it("derives resolved findings from the fixture validation rules", async () => {
    const fix = await fixFinding("proj_food", "f_spof_postgres");
    expect(fix.validation?.resolvedFindingIds).toContain("f_spof_postgres");
    expect(fix.validation?.passes).toBe(true);
  });

  it.each(["Add an analytics database", "Add a reporting database", "Add an analytics db"])(
    "“%s” proposes a single-replica database that fails mock validation",
    async (prompt) => {
      const proposal = await streamProposal("proj_food", input(prompt));
      expect(proposal.changes.map((c) => c.op)).toEqual(["add_node", "add_edge"]);
      expect(proposal.validation?.passes).toBe(false);
      expect(proposal.validation?.newFindings).toEqual([
        {
          severity: "critical",
          title: "Analytics DB is a single point of failure",
          location: "Analytics DB",
        },
      ]);
    },
  );

  it("answers carry no validation and skip validation steps", () => {
    const events = mockProposalEvents({
      id: "p",
      projectId: "proj",
      prompt: "Explain",
      baseVersion: 1,
      kind: "answer",
      recommendation: "R.",
      reason: "Because.",
      impact: [],
      cost: null,
      evidenceIds: [],
      changes: [],
      validation: null,
      status: "pending",
    });
    const labels = new Set(events.flatMap((e) => (e.type === "progress" ? [e.step.label] : [])));
    expect([...labels]).toEqual(["Understanding request", "Drafting answer"]);
  });

  it("chunks text without losing characters", () => {
    const text = "One two three four five six seven.";
    const chunks = chunkText(text, 2);
    expect(chunks.join("")).toBe(text);
    expect(chunks.length).toBeGreaterThan(1);
  });
});
