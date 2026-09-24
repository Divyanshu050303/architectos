import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { HealthPanel } from "@/features/health/components/HealthPanel";
import type { Finding, ValidationReport } from "@/types/validation";

const state = vi.hoisted(() => ({ data: null as unknown }));

vi.mock("@/hooks/use-validation", () => ({
  useValidation: () => ({
    data: state.data,
    isPending: false,
    isError: false,
    error: null,
    refetch: () => {},
  }),
}));

function finding(id: string, overrides: Partial<Finding>): Finding {
  return {
    id,
    ruleId: "rule",
    category: "reliability",
    severity: "high",
    title: id,
    location: "API",
    whyItMatters: "",
    recommendation: "",
    nodeIds: [],
    edgeIds: [],
    evidenceIds: [],
    fixable: false,
    status: "open",
    ...overrides,
  };
}

const REPORT: ValidationReport = {
  projectId: "proj_food",
  architectureVersion: 3,
  validatedAt: "2026-09-18T14:23:00.000Z",
  findings: [
    finding("f_spof", { severity: "critical" }),
    finding("f_timeout", { severity: "high" }),
    finding("f_tracing", { category: "observability", severity: "medium" }),
  ],
  health: {
    overall: 87,
    categories: [
      {
        category: "reliability",
        score: 84,
        findingIds: ["f_spof", "f_timeout"],
        summary: "PostgreSQL is a single point of failure.",
      },
      { category: "observability", score: 72, findingIds: ["f_tracing"], summary: "No tracing." },
      { category: "security", score: 100, findingIds: [], summary: "No issues found." },
    ],
  },
};

describe("HealthPanel", () => {
  beforeEach(() => {
    state.data = REPORT;
  });

  it("links each category to its findings on the validation page", () => {
    render(<HealthPanel projectId="proj_food" />);

    const reliability = screen.getByRole("link", { name: /Reliability/ });
    expect(reliability).toHaveAttribute("href", "/project/proj_food/validation?category=reliability");
    expect(screen.getByRole("link", { name: /Observability/ })).toHaveAttribute(
      "href",
      "/project/proj_food/validation?category=observability",
    );
    for (const link of screen.getAllByRole("link")) {
      expect(link.getAttribute("href")).toMatch(/\?category=/);
    }
  });

  it("explains every score with its findings and summary", () => {
    render(<HealthPanel projectId="proj_food" />);

    expect(screen.getByText("87")).toBeInTheDocument();
    expect(screen.getByText(/Based on 3 findings/)).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL is a single point of failure.")).toBeInTheDocument();
    expect(screen.getByText(/2 findings open · worst critical/)).toBeInTheDocument();
    expect(screen.getByText(/Based on 0 findings · none open/)).toBeInTheDocument();
  });

  it("explains what to do when the architecture has not been validated", () => {
    state.data = null;
    render(<HealthPanel projectId="proj_food" />);

    expect(screen.getByText("Health not calculated yet.")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Go to validation" })).toHaveAttribute(
      "href",
      "/project/proj_food/validation",
    );
  });
});
