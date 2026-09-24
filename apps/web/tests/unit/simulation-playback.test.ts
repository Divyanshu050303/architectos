import { describe, expect, it } from "vitest";

import { buildPlaybackSteps, stepLabel } from "@/features/architecture/utils/simulation-playback";
import type { SimulationTimelineEvent } from "@/types/simulation";

// PostgreSQL failure, shaped like the seeded sim_food_pg_failure timeline (out of order on purpose).
const timeline: SimulationTimelineEvent[] = [
  { atSeconds: 30, phase: "load_increase", nodeIds: ["order_service"], description: "Retries" },
  { atSeconds: 0, phase: "failure", nodeIds: ["postgres"], description: "PostgreSQL unreachable" },
  {
    atSeconds: 15,
    phase: "dependency",
    nodeIds: ["order_service", "payment_service"],
    description: "Lose DB",
  },
  { atSeconds: 60, phase: "resource_pressure", nodeIds: ["order_service"], description: "Pools fill" },
  { atSeconds: 120, phase: "latency", nodeIds: ["api"], description: "Latency rises" },
  { atSeconds: 180, phase: "potential_failure", nodeIds: ["api", "postgres"], description: "May fail" },
];

describe("buildPlaybackSteps", () => {
  const steps = buildPlaybackSteps(timeline);

  it("starts from a normal step and adds one step per event in time order", () => {
    expect(steps).toHaveLength(7);
    expect(steps[0]!.states.size).toBe(0);
    expect(stepLabel(steps[0])).toBe("Normal");
    expect(steps.slice(1).map((s) => stepLabel(s))).toEqual([
      "Failure",
      "Dependency",
      "Load increase",
      "Resource pressure",
      "Latency",
      "Potential failure",
    ]);
  });

  it("walks normal → failed → impacted → cascade cumulatively", () => {
    expect(steps[1]!.states.get("postgres")).toBe("failed");
    expect(steps[1]!.states.has("order_service")).toBe(false);
    expect(steps[2]!.states.get("payment_service")).toBe("impacted");
    expect(steps[5]!.states.get("api")).toBe("impacted");
    expect(steps[6]!.states.get("api")).toBe("cascade");
  });

  it("never downgrades a more severe state", () => {
    // The failed database stays failed when the potential-failure phase names it again.
    expect(steps[6]!.states.get("postgres")).toBe("failed");
  });

  it("tracks the phases reached so far", () => {
    expect([...steps[2]!.reached]).toEqual(["failure", "dependency"]);
    expect(steps[6]!.reached.size).toBe(6);
  });

  it("keeps earlier steps immutable", () => {
    expect(steps[1]!.states.size).toBe(1);
    expect(steps[1]!.states).not.toBe(steps[2]!.states);
  });
});
