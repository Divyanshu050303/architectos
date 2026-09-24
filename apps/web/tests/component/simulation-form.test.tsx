import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { SimulationView } from "@/features/simulation/components/SimulationView";
import type { SimulationScenario } from "@/types/simulation";

const state = vi.hoisted(() => ({
  search: "",
  many: false,
  mutate: vi.fn(),
  replace: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace: state.replace, push: vi.fn() }),
  usePathname: () => "/project/proj_food/simulation",
  useSearchParams: () => new URLSearchParams(state.search),
}));

const SCENARIOS: SimulationScenario[] = [
  {
    id: "scn_pg_failure",
    kind: "database_failure",
    label: "PostgreSQL failure",
    description: "The PostgreSQL primary becomes unreachable.",
    targetNodeIds: ["postgres"],
  },
  {
    id: "scn_redis_failure",
    kind: "redis_failure",
    label: "Redis failure",
    description: "Redis restarts with an empty cache.",
    targetNodeIds: ["redis"],
  },
  {
    id: "scn_region_failure",
    kind: "region_failure",
    label: "Region failure",
    description: "Everything in the region goes down.",
    targetNodeIds: ["postgres", "redis", "kafka"],
  },
  {
    id: "scn_kafka_failure",
    kind: "kafka_failure",
    label: "Kafka outage",
    description: "Kafka stops accepting writes.",
    targetNodeIds: ["kafka"],
  },
];

const EXTRA_SCENARIOS: SimulationScenario[] = [
  {
    id: "scn_api_latency",
    kind: "network_partition",
    label: "API latency",
    description: "The API responds 500 ms slower.",
    targetNodeIds: ["api"],
  },
  {
    id: "scn_traffic_spike",
    kind: "traffic_spike",
    label: "Traffic spike",
    description: "Ten times the usual traffic for the duration.",
    targetNodeIds: ["gateway"],
  },
];

const ok = <T,>(data: T) => ({ data, isPending: false, isError: false, error: null, refetch: vi.fn() });

vi.mock("@/hooks/use-simulation", () => ({
  useSimulationScenarios: () => ok(state.many ? [...SCENARIOS, ...EXTRA_SCENARIOS] : SCENARIOS),
  useLatestSimulation: () => ok(null),
  useSimulationRun: () => ok(undefined),
  useRunSimulation: () => ({ mutate: state.mutate, isPending: false }),
}));

vi.mock("@/hooks/use-architecture", () => ({
  useArchitecture: () => ok(null),
}));

describe("SimulationView form", () => {
  beforeEach(() => {
    state.search = "";
    state.many = false;
    state.mutate.mockReset();
    state.replace.mockReset();
  });

  it("prefills the scenario from ?scenario=", () => {
    state.search = "scenario=scn_redis_failure";
    render(<SimulationView projectId="proj_food" />);
    expect(screen.getByLabelText("Scenario")).toHaveValue("scn_redis_failure");
    expect(screen.getByText("Redis restarts with an empty cache.")).toBeInTheDocument();
  });

  it("prefers a single-target scenario for ?node= from the workspace", () => {
    state.search = "node=kafka";
    render(<SimulationView projectId="proj_food" />);
    expect(screen.getByLabelText("Scenario")).toHaveValue("scn_kafka_failure");
  });

  it("runs the simulation with the chosen config", async () => {
    const user = userEvent.setup();
    state.search = "scenario=scn_pg_failure";
    render(<SimulationView projectId="proj_food" />);

    await user.selectOptions(screen.getByLabelText("Traffic"), "10x");
    await user.selectOptions(screen.getByLabelText("Duration"), "15");
    await user.selectOptions(screen.getByLabelText("Environment"), "staging");
    await user.click(screen.getByRole("button", { name: "Run Simulation" }));

    expect(state.mutate).toHaveBeenCalledTimes(1);
    expect(state.mutate.mock.calls[0]?.[0]).toEqual({
      scenarioId: "scn_pg_failure",
      traffic: "10x",
      durationMinutes: 15,
      environment: "staging",
    });
  });

  it("writes a scenario change to the URL", async () => {
    const user = userEvent.setup();
    render(<SimulationView projectId="proj_food" />);
    await user.selectOptions(screen.getByLabelText("Scenario"), "scn_redis_failure");
    expect(state.replace).toHaveBeenCalledWith("/project/proj_food/simulation?scenario=scn_redis_failure", {
      scroll: false,
    });
  });

  it("makes a long scenario list searchable (Combobox)", async () => {
    const user = userEvent.setup();
    state.many = true;
    state.search = "scenario=scn_pg_failure";
    render(<SimulationView projectId="proj_food" />);

    const picker = screen.getByRole("combobox", { name: "Scenario" });
    expect(picker).toHaveValue("PostgreSQL failure");
    await user.click(picker);
    await user.clear(picker);
    await user.type(picker, "spike");
    expect(within(screen.getByRole("listbox", { name: "Scenarios" })).getAllByRole("option")).toHaveLength(1);
    await user.keyboard("{Enter}");
    expect(state.replace).toHaveBeenCalledWith("/project/proj_food/simulation?scenario=scn_traffic_spike", {
      scroll: false,
    });
    expect(state.mutate).not.toHaveBeenCalled();
  });
});
