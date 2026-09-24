import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { EvolutionTimeline } from "@/features/evolution/components/EvolutionTimeline";
import type { EvolutionStage } from "@/types/evolution";

function stage(id: string, label: string, status: EvolutionStage["status"], dau: number): EvolutionStage {
  return {
    id,
    label,
    dailyActiveUsers: dau,
    status,
    architectureVersion: status === "planned" ? null : 1,
    trigger: "",
    changes: [],
    monthlyCost: 0,
    risk: "low",
    migrationId: null,
    maxSupportedDailyActiveUsers: dau,
  };
}

const STAGES = [
  stage("stage_v1", "V1", "past", 100_000),
  stage("stage_v2", "V2", "current", 5_000_000),
  stage("stage_v3", "V3", "planned", 50_000_000),
];

function Harness({ initial = "stage_v2" }: { initial?: string }) {
  const [selected, setSelected] = useState(initial);
  return (
    <>
      <EvolutionTimeline
        stages={STAGES}
        selectedId={selected}
        onSelect={setSelected}
        panelId="panel"
        idPrefix="t"
      />
      <div role="tabpanel" id="panel">
        {selected}
      </div>
    </>
  );
}

describe("EvolutionTimeline", () => {
  it("shows each stage with DAU and a text status", () => {
    render(<Harness />);
    const tabs = screen.getAllByRole("tab");
    expect(tabs).toHaveLength(3);
    expect(tabs[0]).toHaveTextContent(/V1.*100K DAU.*Past/);
    expect(tabs[1]).toHaveTextContent(/V2.*5M DAU.*Current/);
    expect(tabs[2]).toHaveTextContent(/V3.*50M DAU.*Planned/);
  });

  it("uses a roving tabindex on the selected stage", () => {
    render(<Harness />);
    const [v1, v2, v3] = screen.getAllByRole("tab");
    expect(v2).toHaveAttribute("tabindex", "0");
    expect(v1).toHaveAttribute("tabindex", "-1");
    expect(v3).toHaveAttribute("tabindex", "-1");
  });

  it("moves selection and focus with the arrow keys", async () => {
    const user = userEvent.setup();
    render(<Harness />);
    const v2 = screen.getByRole("tab", { name: /V2/ });
    v2.focus();

    await user.keyboard("{ArrowRight}");
    const v3 = screen.getByRole("tab", { name: /V3/ });
    expect(v3).toHaveAttribute("aria-selected", "true");
    expect(v3).toHaveFocus();
    expect(screen.getByRole("tabpanel")).toHaveTextContent("stage_v3");

    await user.keyboard("{ArrowRight}"); // wraps to the first stage
    expect(screen.getByRole("tab", { name: /V1/ })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{ArrowLeft}");
    expect(screen.getByRole("tab", { name: /V3/ })).toHaveAttribute("aria-selected", "true");

    await user.keyboard("{Home}");
    expect(screen.getByRole("tab", { name: /V1/ })).toHaveFocus();
  });
});
