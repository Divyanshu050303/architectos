import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { foodCost } from "@/api/mock/fixtures-analysis";
import { TooltipProvider } from "@/components/ui/tooltip";
import { CostResults } from "@/features/cost/components/CostView";

import { foodArchitecture } from "./operate-test-utils";

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: vi.fn() }),
  usePathname: () => "/project/proj_food/cost",
  useSearchParams: () => new URLSearchParams(),
}));
vi.mock("@/hooks/use-evidence", () => ({
  useEvidence: (id: string | null) => ({
    data: { id, claim: `Claim for ${id}` },
    isPending: false,
  }),
}));

const arch = foodArchitecture();
const nodeName = (id: string) => arch.nodes.find((n) => n.id === id)?.name ?? id;

describe("CostResults", () => {
  it("shows the backend total and the PostgreSQL row (spec §71)", async () => {
    render(
      <TooltipProvider>
        <CostResults projectId="proj_food" estimate={foodCost()} nodeName={nodeName} />
      </TooltipProvider>,
    );

    expect(screen.getByLabelText("Total $1,240 per month")).toHaveTextContent("$1,240/mo");

    const table = screen.getByRole("table", { name: "Monthly cost by component" });
    const pgRow = within(table).getByRole("row", { name: /PostgreSQL/ });
    expect(pgRow).toHaveTextContent("$184/mo");

    // Sorted by monthly cost, most expensive first.
    const firstDataRow = within(table).getAllByRole("row")[1];
    expect(firstDataRow).toHaveTextContent("$232/mo");

    await userEvent.click(within(pgRow).getByRole("button", { name: /PostgreSQL/ }));
    const breakdown = screen.getByRole("list", { name: "PostgreSQL cost breakdown" });
    expect(breakdown).toHaveTextContent("db.m6g.xlarge instance");
    expect(breakdown).toHaveTextContent("$138");

    expect(screen.getByText("C-003")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Why? Claim for ev_cost_postgres" })).toBeInTheDocument();
  });
});
