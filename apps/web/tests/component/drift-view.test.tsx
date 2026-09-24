import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DriftView } from "@/features/drift/components/DriftView";
import type { DriftItem, DriftReport } from "@/types/discovery";

const state = vi.hoisted(() => ({
  report: null as unknown,
  search: "",
  listeners: new Set<() => void>(),
  checkMutate: (() => {}) as (...args: unknown[]) => void,
}));

vi.mock("next/navigation", async () => {
  const { useSyncExternalStore } = await import("react");
  const subscribe = (listener: () => void) => {
    state.listeners.add(listener);
    return () => state.listeners.delete(listener);
  };
  return {
    usePathname: () => "/project/proj_food/drift",
    useRouter: () => ({
      push: () => {},
      replace: (url: string) => {
        state.search = url.split("?")[1] ?? "";
        for (const listener of state.listeners) listener();
      },
    }),
    useSearchParams: () => new URLSearchParams(useSyncExternalStore(subscribe, () => state.search)),
  };
});

vi.mock("@/hooks/use-drift", () => ({
  useDrift: () => ({ data: state.report, isPending: false, isError: false, error: null, refetch: () => {} }),
  useCheckDrift: () => ({
    mutate: (...args: unknown[]) => state.checkMutate(...args),
    isPending: false,
    isError: false,
    error: null,
  }),
}));

vi.mock("@/hooks/use-architecture", () => ({
  useArchitecture: () => ({
    data: { id: "arch_proj_food", projectId: "proj_food", version: 3, nodes: [], edges: [], assumptions: [] },
    isPending: false,
    isError: false,
    error: null,
    refetch: () => {},
  }),
}));

function item(id: string, overrides: Partial<DriftItem>): DriftItem {
  return {
    id,
    nodeId: id,
    subject: id,
    expected: "1",
    actual: "1",
    severity: "info",
    status: "matching",
    ...overrides,
  };
}

const REPORT: DriftReport = {
  checkedAt: "2026-09-19T07:00:00.000Z",
  source: "aws",
  architectureVersion: 3,
  items: [
    item("api", {
      subject: "API replicas",
      expected: "3",
      actual: "5",
      severity: "medium",
      status: "drifted",
    }),
    item("redis", {
      subject: "Redis",
      expected: "enabled",
      actual: "disabled",
      severity: "high",
      status: "drifted",
    }),
    item("prometheus", {
      subject: "Prometheus",
      expected: "deployed",
      actual: "not found",
      status: "missing",
    }),
    item("ec2", { nodeId: null, subject: "EC2 instance i-0a1f93c2", status: "unexpected", severity: "low" }),
    item("kafka", { subject: "Kafka brokers", expected: "3", actual: "3" }),
  ],
  summary: { drifted: 4, matching: 1 },
};

beforeEach(() => {
  state.report = REPORT;
  state.search = "";
  state.checkMutate = () => {};
});

describe("DriftView", () => {
  it("compares expected with actual and labels every status in text", () => {
    render(<DriftView projectId="proj_food" />);

    const table = screen.getByRole("table", { name: /Expected architecture compared with actual/ });
    expect(within(table).getByRole("columnheader", { name: /Expected/ })).toHaveTextContent(
      "Architecture v3",
    );
    expect(within(table).getByRole("columnheader", { name: /Actual/ })).toHaveTextContent("AWS");

    const api = within(table).getByRole("row", { name: /API replicas/ });
    const cells = within(api).getAllByRole("cell");
    expect(cells.map((c) => c.textContent)).toEqual(expect.arrayContaining(["Drifted", "3", "5", "Medium"]));
    expect(within(table).getByRole("row", { name: /Prometheus/ })).toHaveTextContent("Missing");
    expect(within(table).getByRole("row", { name: /EC2 instance/ })).toHaveTextContent("Unexpected");
    expect(within(table).getByRole("row", { name: /EC2 instance/ })).toHaveTextContent("Not in architecture");

    expect(within(api).getByRole("link", { name: "Locate API replicas on the canvas" })).toHaveAttribute(
      "href",
      "/project/proj_food/architecture?highlight=api&node=api",
    );

    // Summary: counts plus source and checked-at context.
    const summary = screen.getByLabelText("Drift summary");
    expect(summary).toHaveTextContent("Drifted4");
    expect(summary).toHaveTextContent("Matching1");
    expect(screen.getByText(/Checked/).querySelector("time")).toHaveAttribute("dateTime", REPORT.checkedAt);
  });

  it("shows only differences by default and everything with the All filter", async () => {
    const user = userEvent.setup();
    render(<DriftView projectId="proj_food" />);

    expect(screen.queryByRole("row", { name: /Kafka brokers/ })).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Drifted 4" })).toHaveAttribute("aria-pressed", "true");

    await user.click(screen.getByRole("button", { name: "All 5" }));
    expect(state.search).toBe("show=all");
    const kafka = screen.getByRole("row", { name: /Kafka brokers/ });
    expect(kafka).toHaveTextContent("Matching");
  });

  it("explains what to do when drift has not been checked", async () => {
    const user = userEvent.setup();
    const mutate = vi.fn();
    state.report = null;
    state.checkMutate = mutate;
    render(<DriftView projectId="proj_food" />);

    expect(screen.getByText("Drift not checked yet for v3.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Connect a source" })).toHaveAttribute(
      "href",
      "/project/proj_food/infrastructure",
    );
    await user.click(screen.getByRole("button", { name: "Run drift check" }));
    expect(mutate).toHaveBeenCalledTimes(1);
  });
});
