import { render, screen, within } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { foodReliability } from "@/api/mock/fixtures-analysis";
import { TooltipProvider } from "@/components/ui/tooltip";
import { ReliabilityView } from "@/features/reliability/components/ReliabilityView";

import { foodArchitecture, mutationResult, queryResult } from "./operate-test-utils";

const state = vi.hoisted(() => ({ reliability: null as unknown }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/project/proj_food/reliability",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/hooks/use-architecture", () => ({ useArchitecture: () => queryResult(foodArchitecture()) }));
vi.mock("@/hooks/use-reliability", () => ({
  useReliability: () => queryResult(state.reliability),
  useRunReliability: () => mutationResult(),
}));

function renderView() {
  return render(
    <TooltipProvider>
      <ReliabilityView projectId="proj_food" />
    </TooltipProvider>,
  );
}

describe("ReliabilityView", () => {
  it("renders single points of failure with a Locate link into the reliability overlay", () => {
    state.reliability = foodReliability();
    renderView();

    const spofs = screen.getByRole("list", { name: "Single points of failure" });
    expect(within(spofs).getByText("PostgreSQL")).toBeInTheDocument();
    expect(
      within(spofs).getByText("Single primary with no replica or automatic failover."),
    ).toBeInTheDocument();

    const locate = within(spofs).getByRole("link", { name: "Locate PostgreSQL" });
    const href = new URL(locate.getAttribute("href") ?? "", "http://x");
    expect(href.pathname).toBe("/project/proj_food/architecture");
    expect(href.searchParams.get("mode")).toBe("reliability");
    expect(href.searchParams.get("node")).toBe("postgres");
    expect(href.searchParams.get("highlight")?.split(",")).toEqual([
      "postgres",
      "order_service",
      "payment_service",
      "dispatch_worker",
      "api",
    ]);

    expect(screen.getByRole("link", { name: /View reliability overlay/ })).toHaveAttribute(
      "href",
      "/project/proj_food/architecture?mode=reliability",
    );
    expect(screen.getAllByText("99.82%").length).toBeGreaterThan(0);
    expect(screen.getByText("API → Payment Service")).toBeInTheDocument();
  });

  it("explains what to do when reliability has not been analyzed", () => {
    state.reliability = null;
    renderView();

    expect(screen.getByText("Reliability not analyzed yet for v3.")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Run reliability analysis/ })).toBeEnabled();
  });

  it("warns when results belong to an older architecture version", () => {
    state.reliability = { ...foodReliability(), architectureVersion: 2 };
    renderView();

    expect(screen.getByText(/These results are for v2; the architecture is now v3/)).toBeInTheDocument();
  });
});
