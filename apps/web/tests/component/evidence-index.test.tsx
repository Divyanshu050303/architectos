import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

import { ANALYSIS_EVIDENCE } from "@/api/mock/fixtures-analysis";
import { TooltipProvider } from "@/components/ui/tooltip";
import { EvidenceIndex } from "@/features/evidence/components/EvidenceIndex";

const nav = vi.hoisted(() => ({ replace: vi.fn(), search: "" }));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: vi.fn(), replace: nav.replace }),
  usePathname: () => "/project/proj_food/evidence",
  useSearchParams: () => new URLSearchParams(nav.search),
}));
vi.mock("@/hooks/use-evidence", () => ({
  useEvidenceList: () => ({
    data: ANALYSIS_EVIDENCE,
    isPending: false,
    isError: false,
    error: null,
    refetch: vi.fn(),
  }),
}));

function renderIndex() {
  return render(
    <TooltipProvider>
      <EvidenceIndex projectId="proj_food" />
    </TooltipProvider>,
  );
}

describe("EvidenceIndex", () => {
  it("filters evidence by text and groups by kind", async () => {
    nav.search = "";
    renderIndex();

    expect(screen.getByRole("heading", { name: /Calculations/ })).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: /Rules/ })).toBeInTheDocument();

    await userEvent.type(screen.getByRole("searchbox", { name: "Search evidence" }), "postgresql costs");

    expect(screen.getByRole("button", { name: /PostgreSQL costs \$184\/month/ })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /public internet egress/ })).not.toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: /Rules/ })).not.toBeInTheDocument();
    expect(screen.getByText(`1 of ${ANALYSIS_EVIDENCE.length} records match`)).toBeInTheDocument();
  });

  it("selects a record into ?evidence= and shows its details from the URL", async () => {
    nav.search = "evidence=ev_cost_postgres";
    nav.replace.mockClear();
    renderIndex();

    const details = screen.getByRole("region", { name: "Details" });
    expect(within(details).getByText("db.m6g.xlarge on-demand")).toBeInTheDocument();
    expect(within(details).getByText("On-demand us-east-1 prices, 730 hours per month.")).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: /Payment Service reaches the provider/ }));
    expect(nav.replace).toHaveBeenCalledWith(
      "/project/proj_food/evidence?evidence=ev_threat_payment_egress",
      { scroll: false },
    );
  });
});
