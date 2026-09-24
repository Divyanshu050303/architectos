import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { FindingCard } from "@/features/validation/components/FindingCard";
import type { Finding } from "@/types/validation";

const mocks = vi.hoisted(() => ({
  push: vi.fn(),
  setStatus: vi.fn(),
  fix: vi.fn(),
}));

vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: mocks.push, replace: vi.fn(), back: vi.fn(), prefetch: vi.fn() }),
}));
vi.mock("@/hooks/use-validation", () => ({
  useSetFindingStatus: () => ({ mutate: mocks.setStatus, isPending: false }),
}));
vi.mock("@/hooks/use-proposals", () => ({
  useFixFinding: () => ({ mutateAsync: mocks.fix, isPending: false }),
}));

const FINDING: Finding = {
  id: "f_timeout",
  ruleId: "reliability.missing_timeout",
  category: "reliability",
  severity: "high",
  title: "Missing timeout",
  location: "API → Payment Service",
  whyItMatters: "External dependency can block request workers.",
  recommendation: "Add timeout <= configured SLA.",
  nodeIds: ["api", "payment_service"],
  edgeIds: ["e_api_payment"],
  evidenceIds: ["ev_api_payment_timeout"],
  fixable: false,
  status: "open",
};

function renderCard(finding: Finding = FINDING) {
  return render(
    <TooltipProvider>
      <FindingCard projectId="proj_food" finding={finding} />
    </TooltipProvider>,
  );
}

describe("FindingCard", () => {
  beforeEach(() => {
    mocks.push.mockReset();
    mocks.setStatus.mockReset();
    mocks.fix.mockReset();
  });

  it("renders the title, location, why it matters and recommendation", () => {
    renderCard();
    expect(screen.getByRole("heading", { level: 3, name: "Missing timeout" })).toBeInTheDocument();
    expect(screen.getByText("API → Payment Service")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Why it matters" })).toBeInTheDocument();
    expect(screen.getByText("External dependency can block request workers.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Recommendation" })).toBeInTheDocument();
    expect(screen.getByText("Add timeout <= configured SLA.")).toBeInTheDocument();
    // Severity is conveyed in text, not colour alone.
    expect(screen.getByText("High")).toBeInTheDocument();
  });

  it("Locate navigates to the canvas with the affected nodes highlighted", async () => {
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: "Locate" }));
    expect(mocks.push).toHaveBeenCalledWith(
      "/project/proj_food/architecture?highlight=api,payment_service&node=api",
    );
  });

  it("Ignore calls the status mutation", async () => {
    renderCard();
    await userEvent.click(screen.getByRole("button", { name: "Ignore" }));
    expect(mocks.setStatus).toHaveBeenCalledWith(
      { findingId: "f_timeout", status: "ignored" },
      expect.any(Object),
    );
  });

  it("offers Restore for an ignored finding", async () => {
    renderCard({ ...FINDING, status: "ignored" });
    await userEvent.click(screen.getByRole("button", { name: "Restore" }));
    expect(mocks.setStatus).toHaveBeenCalledWith(
      { findingId: "f_timeout", status: "open" },
      expect.any(Object),
    );
  });

  it("hides Fix when the finding is not fixable", () => {
    renderCard();
    expect(screen.queryByRole("button", { name: "Fix" })).not.toBeInTheDocument();
  });

  it("Fix creates a proposal and opens it in the workspace", async () => {
    mocks.fix.mockResolvedValue({ id: "prop_42" });
    renderCard({ ...FINDING, fixable: true });
    await userEvent.click(screen.getByRole("button", { name: "Fix" }));
    expect(mocks.fix).toHaveBeenCalledWith("f_timeout");
    await waitFor(() =>
      expect(mocks.push).toHaveBeenCalledWith("/project/proj_food/architecture?proposal=prop_42"),
    );
  });

  it("disables Explain and Locate when there is nothing to show", () => {
    renderCard({ ...FINDING, evidenceIds: [], nodeIds: [] });
    expect(screen.getByRole("button", { name: "Explain" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "Locate" })).toBeDisabled();
  });
});
