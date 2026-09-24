import { render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { ComparisonView } from "@/features/evolution/components/ComparisonView";
import type { ArchitectureComparison } from "@/types/evolution";

const COMPARISON: ArchitectureComparison = {
  from: { label: "V1", version: 1 },
  to: { label: "V2", version: 2 },
  components: [
    { change: "added", nodeId: "redis", name: "Redis", type: "cache", details: [] },
    { change: "added", nodeId: "cdn", name: "CDN", type: "cdn", details: [] },
    { change: "removed", nodeId: "legacy", name: "Legacy Cron", type: "service", details: [] },
    {
      change: "changed",
      nodeId: "postgres",
      name: "PostgreSQL",
      type: "database",
      details: [{ field: "replicas", before: 1, after: 3 }],
    },
  ],
  connections: [
    {
      change: "added",
      edgeId: "e_api_redis",
      sourceName: "API",
      targetName: "Redis",
      details: [],
    },
  ],
  capacity: { beforeMaxDailyActiveUsers: 2_000_000, afterMaxDailyActiveUsers: 7_000_000 },
  cost: { before: 410, after: 720, currency: "USD" },
};

/** Text a screen reader announces: aria-hidden glyphs removed. */
function accessibleText(el: HTMLElement): string {
  const clone = el.cloneNode(true) as HTMLElement;
  clone.querySelectorAll("[aria-hidden]").forEach((n) => n.remove());
  return clone.textContent ?? "";
}

describe("ComparisonView", () => {
  it("renders the version header", () => {
    render(<ComparisonView comparison={COMPARISON} />);
    expect(screen.getByRole("heading", { name: "V1 → V2" })).toBeInTheDocument();
  });

  it("marks added, removed and changed components with text, not colour alone", () => {
    render(<ComparisonView comparison={COMPARISON} />);
    const components = screen.getByRole("region", { name: "Components" });
    const items = within(components).getAllByRole("listitem").map(accessibleText);
    expect(items).toEqual([
      "Added: Redis",
      "Added: CDN",
      "Removed: Legacy Cron",
      "Changed: PostgreSQL replicas 1 → 3",
    ]);
  });

  it("lists connection changes", () => {
    render(<ComparisonView comparison={COMPARISON} />);
    const connections = screen.getByRole("region", { name: "Connections" });
    expect(within(connections).getByText("API → Redis")).toBeInTheDocument();
  });

  it("shows capacity and cost before → after", () => {
    render(<ComparisonView comparison={COMPARISON} />);
    const capacity = screen.getByText("Capacity").closest("div");
    const cost = screen.getByText("Cost").closest("div");
    expect(capacity).toHaveTextContent(/2M\s*to\s*7M\s*DAU/);
    expect(cost).toHaveTextContent(/\$410\s*to\s*\$720\s*\/month/);
  });
});
