import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import {
  OperatingEnvelopeChart,
  versionColumns,
} from "@/features/capacity/components/OperatingEnvelopeChart";
import type { EnvelopePoint } from "@/types/capacity";
import type { EvolutionStage } from "@/types/evolution";

const POINTS: EnvelopePoint[] = [
  { label: "100K", dailyActiveUsers: 100_000, status: "supported" },
  { label: "1M", dailyActiveUsers: 1_000_000, status: "supported" },
  { label: "2.4M", dailyActiveUsers: 2_400_000, status: "current" },
  { label: "7.8M", dailyActiveUsers: 7_800_000, status: "warning" },
  { label: "10M", dailyActiveUsers: 10_000_000, status: "exceeded" },
];

describe("OperatingEnvelopeChart", () => {
  it("summarizes every point and marks the current load in its accessible name", () => {
    render(<OperatingEnvelopeChart points={POINTS} maxSupportedDailyActiveUsers={7_800_000} />);

    const chart = screen.getByRole("img");
    const summary = chart.getAttribute("aria-label") ?? "";
    expect(summary).toContain("Supported up to 7.8M DAU");
    for (const point of POINTS) expect(summary).toContain(`${point.label} DAU`);
    expect(summary).toContain("2.4M DAU: Current load");
    expect(summary).toContain("7.8M DAU: Warning");
    expect(summary).toContain("10M DAU: Exceeded");
  });

  it("exposes the data as a table for screen readers", () => {
    render(<OperatingEnvelopeChart points={POINTS} maxSupportedDailyActiveUsers={7_800_000} />);

    const table = screen.getByRole("table", { name: "Operating envelope data points" });
    const rows = within(table).getAllByRole("row").slice(1);
    expect(rows).toHaveLength(POINTS.length);
    const current = within(table).getByRole("rowheader", { name: "2.4M" }).closest("tr");
    expect(current).toHaveTextContent("2,400,000");
    expect(current).toHaveTextContent("Current load");
  });

  it("explains an empty envelope instead of drawing nothing", () => {
    render(<OperatingEnvelopeChart points={[]} maxSupportedDailyActiveUsers={0} />);
    expect(screen.getByText("The capacity engine returned no envelope points.")).toBeInTheDocument();
  });
});

function stage(
  label: string,
  status: EvolutionStage["status"],
  maxSupportedDailyActiveUsers: number,
): EvolutionStage {
  return {
    id: `stage_${label}`,
    label,
    dailyActiveUsers: maxSupportedDailyActiveUsers / 2,
    status,
    architectureVersion: status === "planned" ? null : 1,
    trigger: "",
    changes: [],
    monthlyCost: 0,
    risk: "low",
    migrationId: null,
    maxSupportedDailyActiveUsers,
  };
}

const STAGES = [
  stage("V1", "past", 2_000_000),
  stage("V2", "current", 7_800_000),
  stage("V3", "planned", 60_000_000),
];

describe("OperatingEnvelopeChart by version (spec §36)", () => {
  it("keeps the backend statuses on the current stage and compares other stages with their maximum", () => {
    const columns = versionColumns(STAGES, POINTS);
    expect(columns?.map((c) => c.stage.label)).toEqual(["V1", "V2", "V3"]);
    const statuses = (label: string) =>
      columns?.find((c) => c.stage.label === label)?.points.map((p) => p.status);
    expect(statuses("V2")).toEqual(POINTS.map((p) => p.status));
    expect(statuses("V1")).toEqual(["supported", "supported", "exceeded", "exceeded", "exceeded"]);
    expect(statuses("V3")).toEqual(["supported", "supported", "supported", "supported", "supported"]);
  });

  it("falls back to the load view without a usable roadmap", () => {
    expect(versionColumns([], POINTS)).toBeNull();
    expect(versionColumns([stage("V1", "current", 1)], POINTS)).toBeNull();
    expect(versionColumns([stage("V1", "past", 1), stage("V2", "planned", 2)], POINTS)).toBeNull();

    render(<OperatingEnvelopeChart points={POINTS} maxSupportedDailyActiveUsers={7_800_000} stages={[]} />);
    expect(screen.getByRole("table", { name: "Operating envelope data points" })).toBeInTheDocument();
  });

  it("summarizes each version and exposes a version × load table", () => {
    render(
      <OperatingEnvelopeChart points={POINTS} maxSupportedDailyActiveUsers={7_800_000} stages={STAGES} />,
    );
    const summary = screen.getByRole("img").getAttribute("aria-label") ?? "";
    expect(summary).toMatch(/^Operating envelope by architecture version/);
    expect(summary).toContain("V1 (past) supports up to 2M DAU");
    expect(summary).toContain(
      "V2 (current) supports up to 7.8M DAU: 100K supported, 1M supported, 2.4M current load",
    );
    expect(summary).toContain("10M exceeded");

    const table = screen.getByRole("table", { name: "Operating envelope by architecture version" });
    const headers = within(table)
      .getAllByRole("columnheader")
      .map((h) => h.textContent);
    expect(headers).toEqual(["Load level", "V1 (past)", "V2 (current)", "V3 (planned)"]);
    const row = within(table)
      .getByRole("rowheader", { name: /^2\.4M/ })
      .closest("tr");
    expect(row).toHaveTextContent("Exceeded");
    expect(row).toHaveTextContent("Current load");
    expect(row).toHaveTextContent("Supported");
    const max = within(table).getByRole("rowheader", { name: "Maximum supported DAU" }).closest("tr");
    expect(max).toHaveTextContent("60,000,000");
  });
});
