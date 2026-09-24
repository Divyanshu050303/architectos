import { describe, expect, it } from "vitest";

import {
  collapseByDomain,
  defaultGraphView,
  domainFromNodeId,
  domainLabel,
  hasDomains,
  neighbourhood,
} from "@/features/architecture/utils/graph-view";

const nodes = [
  { id: "web", domain: "edge" },
  { id: "gw", domain: "edge" },
  { id: "pay", domain: "payments" },
  { id: "pay_db", domain: "payments" },
  { id: "fraud", domain: "payments" },
  { id: "ord", domain: "orders" },
  { id: "ord_db", domain: "orders" },
  { id: "misc" },
];
const edges = [
  { id: "e1", source: "web", target: "gw" },
  { id: "e2", source: "gw", target: "pay" },
  { id: "e3", source: "gw", target: "ord" },
  { id: "e4", source: "pay", target: "pay_db" },
  { id: "e5", source: "pay", target: "fraud" },
  { id: "e6", source: "ord", target: "ord_db" },
  { id: "e7", source: "ord", target: "pay" },
  { id: "e8", source: "gw", target: "fraud" },
];

describe("collapseByDomain", () => {
  it("collapses every domain into one node and folds connections between domains", () => {
    const view = collapseByDomain(nodes, edges, null);
    expect(view.domains.map((d) => [d.domain, d.nodeIds.length])).toEqual([
      ["edge", 2],
      ["payments", 3],
      ["orders", 2],
      ["other", 1],
    ]);
    expect(view.hiddenNodeIds.size).toBe(nodes.length);
    // gw→pay and gw→fraud fold into one edge → payments connection; internal edges disappear.
    const folded = view.aggregatedEdges.map((e) => [e.source, e.target, e.edgeIds.length]);
    expect(folded).toEqual([
      ["domain:edge", "domain:payments", 2],
      ["domain:edge", "domain:orders", 1],
      ["domain:orders", "domain:payments", 1],
    ]);
    expect(view.hiddenEdgeIds.size).toBe(edges.length);
    for (const d of view.domains) expect(view.positions[d.id]).toBeDefined();
  });

  it("expands one domain and keeps the others collapsed", () => {
    const view = collapseByDomain(nodes, edges, "payments");
    expect(view.domains.map((d) => d.domain)).toEqual(["edge", "orders", "other"]);
    expect(view.hiddenNodeIds.has("pay")).toBe(false);
    expect(view.hiddenNodeIds.has("ord")).toBe(true);
    // Connections into the expanded domain now land on its components.
    const folded = view.aggregatedEdges.map((e) => `${e.source}->${e.target}:${e.edgeIds.join(",")}`);
    expect(view.aggregatedEdges.every((e) => !e.bidirectional)).toBe(true);
    expect(folded).toContain("domain:edge->pay:e2");
    expect(folded).toContain("domain:edge->fraud:e8");
    expect(folded).toContain("domain:orders->pay:e7");
    // Real edges inside the expanded domain stay as they are.
    expect(view.hiddenEdgeIds.has("e4")).toBe(false);
    expect(view.positions.pay).toBeDefined();
    expect(view.positions["domain:orders"]).toBeDefined();
  });

  it("folds connections running both ways into one bidirectional line", () => {
    const view = collapseByDomain(nodes, [...edges, { id: "e9", source: "pay", target: "gw" }], null);
    const pair = view.aggregatedEdges.find((e) => e.id === "agg:domain:edge->domain:payments");
    expect(pair).toMatchObject({ bidirectional: true, edgeIds: ["e2", "e8", "e9"] });
  });
});

describe("graph view helpers", () => {
  it("opens large architectures with domains in overview", () => {
    expect(defaultGraphView(119, true)).toBe("overview");
    expect(defaultGraphView(119, false)).toBe("detailed");
    expect(defaultGraphView(11, true)).toBe("detailed");
  });

  it("detects domains, labels them and parses domain node ids", () => {
    expect(hasDomains(nodes)).toBe(true);
    expect(hasDomains([{}, { domain: " " }])).toBe(false);
    expect(domainLabel("order_fulfillment")).toBe("Order fulfillment");
    expect(domainLabel("other")).toBe("Other");
    expect(domainFromNodeId("domain:payments")).toBe("payments");
    expect(domainFromNodeId("payments")).toBeNull();
  });

  it("finds the 1- and 2-hop neighbourhood regardless of direction", () => {
    expect([...neighbourhood(edges, "pay", 1)].sort()).toEqual(["fraud", "gw", "ord", "pay", "pay_db"]);
    expect(neighbourhood(edges, "pay_db", 1)).toEqual(new Set(["pay_db", "pay"]));
    expect(neighbourhood(edges, "pay_db", 2)).toEqual(new Set(["pay_db", "pay", "fraud", "gw", "ord"]));
  });
});
