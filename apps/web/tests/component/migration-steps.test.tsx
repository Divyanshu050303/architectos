import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { MigrationDependencyGraph } from "@/features/migration/components/MigrationDependencyGraph";
import { MigrationSteps } from "@/features/migration/components/MigrationSteps";
import type { MigrationStep } from "@/types/evolution";

function step(order: number, title: string, dependsOn: string[], overrides: Partial<MigrationStep> = {}) {
  return {
    id: `s${order}`,
    order,
    title,
    description: `${title} description`,
    dependsOn,
    risk: "low",
    rollback: `Undo ${title.toLowerCase()}`,
    estimatedDuration: "2 days",
    nodeIds: ["postgres"],
    status: "pending",
    ...overrides,
  } satisfies MigrationStep;
}

// Deliberately out of order: the component orders by `order`.
const STEPS: MigrationStep[] = [
  step(3, "Route reads to replicas", ["s2"], { risk: "medium", status: "in_progress" }),
  step(1, "Add PgBouncer", [], { status: "done" }),
  step(2, "Add replicas", ["s1"], { risk: "high" }),
];

const names: Record<string, string> = { postgres: "PostgreSQL" };
const nodeName = (id: string) => names[id] ?? id;

describe("MigrationSteps", () => {
  it("renders steps in dependency order with their dependencies", () => {
    render(<MigrationSteps steps={STEPS} nodeName={nodeName} />);
    const items = within(screen.getByRole("list", { name: "Migration steps" })).getAllByRole("listitem", {
      name: undefined,
    });
    const headings = screen.getAllByRole("heading", { level: 3 }).map((h) => h.textContent);
    expect(headings).toEqual([
      "Step 1: Add PgBouncer",
      "Step 2: Add replicas",
      "Step 3: Route reads to replicas",
    ]);
    expect(items.length).toBeGreaterThanOrEqual(3);

    const dep = screen.getByRole("link", { name: "Step 2: Add replicas" });
    expect(dep).toHaveAttribute("href", "#migration-step-s2");
    expect(screen.getByText("Nothing — can start first")).toBeInTheDocument();
  });

  it("shows status and risk as text", () => {
    render(<MigrationSteps steps={STEPS} nodeName={nodeName} />);
    expect(screen.getByText("Done")).toBeInTheDocument();
    expect(screen.getByText("In progress")).toBeInTheDocument();
    expect(screen.getByText("High risk")).toBeInTheDocument();
    expect(screen.getAllByText("PostgreSQL")).toHaveLength(3);
  });

  it("keeps each rollback in a disclosure", async () => {
    const user = userEvent.setup();
    render(<MigrationSteps steps={STEPS} nodeName={nodeName} />);
    const rollback = screen.getByText("Undo add replicas");
    const details = rollback.closest("details");
    expect(details).not.toHaveAttribute("open");

    await user.click(within(details as HTMLElement).getByText("Rollback"));
    expect(details).toHaveAttribute("open");
  });
});

describe("MigrationDependencyGraph", () => {
  it("summarises the graph accessibly", () => {
    render(<MigrationDependencyGraph steps={STEPS} />);
    expect(screen.getByRole("img")).toHaveAccessibleName(
      "Dependency graph of 3 steps. Step 1 can start first.",
    );
    expect(screen.getByText("Step 3 depends on step 2.")).toBeInTheDocument();
  });
});
