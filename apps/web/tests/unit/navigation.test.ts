import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
  ALL_PROJECT_NAV_ITEMS,
  isNavigable,
  isNavItemActive,
  PROJECT_NAV,
  projectHref,
} from "@/config/navigation";

// Application routes live in the (app) route group (spec §117); the group adds no URL segment.
const APP_GROUP_DIR = join(process.cwd(), "app", "(app)");
const PROJECT_ROUTE_DIR = join(APP_GROUP_DIR, "project", "[projectId]");

function routeFile(segment: string): string {
  return join(PROJECT_ROUTE_DIR, segment, "page.tsx");
}

describe("project navigation", () => {
  it("follows the spec §21 grouping", () => {
    expect(PROJECT_NAV.map((g) => [g.label, g.items.map((i) => i.label)])).toEqual([
      ["Project", ["Overview"]],
      ["Design", ["Requirements", "Architecture"]],
      ["Analyze", ["Capacity", "Validation", "Simulation"]],
      ["Operate", ["Reliability", "Security", "Observability", "Cost"]],
      ["Evolve", ["Evolution", "Migration"]],
      ["Discover", ["Infrastructure", "Drift"]],
      ["Document", ["Decisions", "Evidence", "Reports"]],
    ]);
  });

  it("uses the expected route segment for every item", () => {
    expect(Object.fromEntries(ALL_PROJECT_NAV_ITEMS.map((i) => [i.label, i.segment]))).toEqual({
      Overview: "",
      Requirements: "requirements",
      Architecture: "architecture",
      Capacity: "capacity",
      Validation: "validation",
      Simulation: "simulation",
      Reliability: "reliability",
      Security: "security",
      Observability: "observability",
      Cost: "cost",
      Evolution: "evolution",
      Migration: "migration",
      Infrastructure: "infrastructure",
      Drift: "drift",
      Decisions: "decisions",
      Evidence: "evidence",
      Reports: "reports",
      Settings: "settings",
    });
  });

  it("makes every item available (linked, no planned-surface state)", () => {
    for (const item of ALL_PROJECT_NAV_ITEMS) {
      expect(item.hasRoute, item.label).toBe(true);
      expect(isNavigable(item), item.label).toBe(true);
    }
  });

  it.each(ALL_PROJECT_NAV_ITEMS.map((i) => [i.label, i.segment]))(
    "%s has a route file",
    (_label, segment) => {
      expect(existsSync(routeFile(segment))).toBe(true);
    },
  );

  it("keeps the application in the (app) group, entered at /app", () => {
    for (const route of ["app", "dashboard", "projects"]) {
      expect(existsSync(join(APP_GROUP_DIR, route, "page.tsx")), route).toBe(true);
      expect(existsSync(join(process.cwd(), "app", route)), `${route} outside the group`).toBe(false);
    }
    expect(existsSync(join(process.cwd(), "app", "project"))).toBe(false);
  });

  it("marks the overview active only on the project root", () => {
    const [overview] = ALL_PROJECT_NAV_ITEMS;
    const capacity = ALL_PROJECT_NAV_ITEMS.find((i) => i.segment === "capacity");
    if (!overview || !capacity) throw new Error("missing nav items");
    expect(isNavItemActive("/project/p1", "p1", overview)).toBe(true);
    expect(isNavItemActive("/project/p1/capacity", "p1", overview)).toBe(false);
    expect(isNavItemActive("/project/p1/capacity", "p1", capacity)).toBe(true);
    expect(projectHref("a b", "capacity")).toBe("/project/a%20b/capacity");
  });
});
