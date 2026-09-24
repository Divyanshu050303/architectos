import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { EvidenceDrawer } from "@/features/evidence/components/EvidenceDrawer";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { useUiStore } from "@/stores/ui-store";
import type { Evidence } from "@/types/architecture";

const EVIDENCE: Evidence = {
  id: "ev_pg_connections",
  claim: "PostgreSQL is approaching connection capacity",
  kind: "calculation",
  calculations: [
    { label: "Expected connections", value: 438 },
    { label: "Configured maximum", value: 500 },
    { label: "Threshold", value: "7.8M", unit: "DAU" },
  ],
  source: "Capacity model CM-182",
  assumptions: [
    { id: "A-001", statement: "Peak traffic is 3x the daily average." },
    { id: "A-007", statement: "Each service instance holds a pool of 20 connections." },
  ],
};

vi.mock("@/hooks/use-evidence", () => ({
  useEvidence: (id: string | null) =>
    id === EVIDENCE.id
      ? { data: EVIDENCE, isPending: false, isError: false, error: null, refetch: vi.fn() }
      : { data: undefined, isPending: true, isError: false, error: null, refetch: vi.fn() },
}));

function renderWithDrawer(ui: React.ReactNode = null) {
  return render(
    <TooltipProvider>
      {ui}
      <EvidenceDrawer />
    </TooltipProvider>,
  );
}

describe("EvidenceDrawer", () => {
  afterEach(() => useUiStore.getState().reset());

  it("renders claim, calculations, source and assumptions", () => {
    useUiStore.getState().openEvidence("ev_pg_connections");
    renderWithDrawer();

    const drawer = screen.getByRole("dialog", { name: "Evidence" });
    expect(within(drawer).getByText("PostgreSQL is approaching connection capacity")).toBeInTheDocument();

    const expected = within(drawer).getByText("Expected connections").closest("div");
    expect(expected).toHaveTextContent("438");
    const threshold = within(drawer).getByText("Threshold").closest("div");
    expect(threshold).toHaveTextContent("7.8MDAU");

    expect(within(drawer).getByText("Capacity model CM-182")).toBeInTheDocument();
    expect(within(drawer).getByText("A-001")).toBeInTheDocument();
    expect(
      within(drawer).getByText("Each service instance holds a pool of 20 connections."),
    ).toBeInTheDocument();
    expect(within(drawer).getByText("Calculation", { selector: "span" })).toBeInTheDocument();
  });

  it("is closed until a Why? button opens it", async () => {
    renderWithDrawer(<WhyButton evidenceId="ev_pg_connections" subject="PostgreSQL connections" />);
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Why? PostgreSQL connections" }));
    expect(screen.getByRole("dialog", { name: "Evidence" })).toBeInTheDocument();

    await userEvent.click(screen.getByRole("button", { name: "Close" }));
    expect(useUiStore.getState().evidenceId).toBeNull();
  });

  it("disables Why? when there is no evidence", () => {
    renderWithDrawer(<WhyButton evidenceId={null} />);
    expect(screen.getByRole("button", { name: "Why?" })).toBeDisabled();
  });
});
