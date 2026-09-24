import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ArchitectureNodeCard, sameNodeData } from "@/features/architecture/components/ArchitectureNode";
import { type CanvasNodeData, describeNode } from "@/features/architecture/hooks/useArchitectureCanvas";

function makeData(overrides: Partial<CanvasNodeData> = {}): CanvasNodeData {
  return {
    node: {
      id: "postgres",
      type: "database",
      name: "PostgreSQL",
      technology: "PostgreSQL 16",
      description: "Primary store",
      configuration: { replicas: 2 },
      position: { x: 0, y: 0 },
    },
    category: "Database",
    status: "healthy",
    statusLabel: "Healthy",
    metric: { label: "CPU", value: "42%" },
    utilization: 0.42,
    badges: [],
    highlighted: false,
    dimmed: false,
    mode: "topology",
    preview: null,
    direction: "TB",
    ...overrides,
  };
}

describe("ArchitectureNodeCard", () => {
  it("renders category, name, technology, metric and status text", () => {
    render(<ArchitectureNodeCard data={makeData()} />);
    expect(screen.getByText("Database")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL")).toBeInTheDocument();
    expect(screen.getByText("PostgreSQL 16")).toBeInTheDocument();
    expect(screen.getByText("42%")).toBeInTheDocument();
    expect(screen.getByText("CPU")).toBeInTheDocument();
    // Status is text, not colour alone (spec §62).
    expect(screen.getByText("Healthy")).toBeInTheDocument();
    expect(screen.getByText("Status:")).toHaveClass("sr-only");
  });

  it("exposes the selected state as text and a data attribute", () => {
    const { container } = render(<ArchitectureNodeCard data={makeData()} selected />);
    expect(screen.getByText("Selected")).toHaveClass("sr-only");
    expect(container.firstElementChild).toHaveAttribute("data-selected", "true");
  });

  it("exposes critical status and badges accessibly", () => {
    const { container } = render(
      <ArchitectureNodeCard
        data={makeData({
          status: "critical",
          statusLabel: "Critical",
          badges: [{ label: "Bottleneck", tone: "danger" }],
          mode: "capacity",
          utilization: 0.92,
          metric: { label: "CPU", value: "92%" },
        })}
      />,
    );
    expect(container.firstElementChild).toHaveAttribute("data-status", "critical");
    expect(screen.getByText("Critical")).toBeInTheDocument();
    expect(screen.getByText("Bottleneck")).toBeInTheDocument();
    expect(screen.getByRole("meter", { name: /PostgreSQL CPU 92%/ })).toHaveAttribute("aria-valuenow", "92");
  });

  it("marks proposal previews without relying on colour", () => {
    render(<ArchitectureNodeCard data={makeData({ preview: "added" })} />);
    expect(screen.getByText("✦ Proposed")).toBeInTheDocument();
  });

  it("summarises the node for screen readers", () => {
    const label = describeNode(
      makeData({ status: "critical", statusLabel: "Critical", badges: [{ label: "SPOF", tone: "danger" }] }),
    );
    expect(label).toBe("PostgreSQL, Database, PostgreSQL 16, status Critical, CPU 42%, SPOF");
  });

  it("compares node data field by field for memoization", () => {
    const a = makeData();
    expect(sameNodeData(a, { ...a, metric: { label: "CPU", value: "42%" }, badges: [] })).toBe(true);
    expect(sameNodeData(a, { ...a, status: "warning" })).toBe(false);
  });
});
