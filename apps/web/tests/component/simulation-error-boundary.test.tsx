import { render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { SimulationView } from "@/features/simulation/components/SimulationView";
import type { SimulationRun, SimulationScenario } from "@/types/simulation";

/** Spec §82: a simulation failure must not crash the surrounding page. */

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: vi.fn(), push: vi.fn() }),
  usePathname: () => "/project/proj_food/simulation",
  useSearchParams: () => new URLSearchParams(),
}));

vi.mock("@/features/simulation/components/SimulationResultCard", () => ({
  SimulationResultCard: () => {
    throw new Error("result card exploded");
  },
}));

const SCENARIO: SimulationScenario = {
  id: "scn_pg_failure",
  kind: "database_failure",
  label: "PostgreSQL failure",
  description: "The PostgreSQL primary becomes unreachable.",
  targetNodeIds: ["postgres"],
};

const RUN: SimulationRun = {
  id: "sim_1",
  projectId: "proj_food",
  architectureVersion: 3,
  scenarioId: SCENARIO.id,
  config: { scenarioId: SCENARIO.id, traffic: "current", durationMinutes: 5, environment: "production_like" },
  status: "succeeded",
  steps: [],
  result: {
    impact: "high",
    affectedNodeIds: ["postgres"],
    metrics: [],
    errorRate: { before: 0, after: 0.2 },
    cascadingFailure: "potential",
    timeline: [{ atSeconds: 0, phase: "failure", nodeIds: ["postgres"], description: "PostgreSQL is down." }],
    evidenceIds: [],
  },
  error: null,
};

const ok = <T,>(data: T) => ({ data, isPending: false, isError: false, error: null, refetch: vi.fn() });

vi.mock("@/hooks/use-simulation", () => ({
  useSimulationScenarios: () => ok([SCENARIO]),
  useLatestSimulation: () => ok(RUN),
  useSimulationRun: () => ok(undefined),
  useRunSimulation: () => ({ mutate: vi.fn(), isPending: false }),
}));

vi.mock("@/hooks/use-architecture", () => ({ useArchitecture: () => ok(null) }));

describe("SimulationView error boundaries", () => {
  beforeEach(() => {
    // React logs caught render errors; keep the test output clean.
    vi.spyOn(console, "error").mockImplementation(() => {});
  });
  afterEach(() => vi.restoreAllMocks());

  it("contains a crashing result card and keeps the rest of the page working", () => {
    render(<SimulationView projectId="proj_food" />);

    expect(screen.getByText("Simulation result could not be displayed.")).toBeInTheDocument();
    // Heading, settings form and propagation timeline still render.
    expect(screen.getByRole("heading", { name: "Simulation", level: 1 })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Run Simulation" })).toBeEnabled();
    expect(screen.getByText("PostgreSQL is down.")).toBeInTheDocument();
  });
});
