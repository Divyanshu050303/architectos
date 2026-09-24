import { describe, expect, it } from "vitest";

import { NODE_HEIGHT, NODE_WIDTH } from "@/features/architecture/constants";
import {
  autoLayout,
  layoutByDomain,
  layoutForceDirected,
  layoutGraph,
} from "@/features/architecture/utils/graph-layout";

const nodes = [{ id: "client" }, { id: "api" }, { id: "db" }, { id: "cache" }];
const edges = [
  { source: "client", target: "api" },
  { source: "api", target: "db" },
  { source: "api", target: "cache" },
];

function overlaps(a: { x: number; y: number }, b: { x: number; y: number }) {
  return Math.abs(a.x - b.x) < NODE_WIDTH && Math.abs(a.y - b.y) < NODE_HEIGHT;
}

describe("layoutGraph", () => {
  it("returns a position for every node", () => {
    const positions = layoutGraph(nodes, edges, { direction: "TB" });
    expect(Object.keys(positions).sort()).toEqual(["api", "cache", "client", "db"]);
  });

  it("places dependencies below their callers top-to-bottom", () => {
    const p = layoutGraph(nodes, edges, { direction: "TB" });
    expect(p.client!.y).toBeLessThan(p.api!.y);
    expect(p.api!.y).toBeLessThan(p.db!.y);
    expect(overlaps(p.db!, p.cache!)).toBe(false);
  });

  it("places dependencies to the right left-to-right", () => {
    const p = layoutGraph(nodes, edges, { direction: "LR" });
    expect(p.client!.x).toBeLessThan(p.api!.x);
    expect(p.api!.x).toBeLessThan(p.db!.x);
  });

  it("ignores edges to unknown nodes and self-loops", () => {
    const p = layoutGraph(
      [{ id: "a" }],
      [
        { source: "a", target: "a" },
        { source: "a", target: "ghost" },
      ],
      { direction: "TB" },
    );
    expect(Object.keys(p)).toEqual(["a"]);
  });
});

/** Largest overlap (px) between any two node boxes; 0 when none touch. */
function worstOverlap(positions: Record<string, { x: number; y: number }>): number {
  const list = Object.values(positions);
  let worst = 0;
  for (let i = 0; i < list.length; i++) {
    for (let j = i + 1; j < list.length; j++) {
      const a = list[i]!;
      const b = list[j]!;
      const ox = NODE_WIDTH - Math.abs(a.x - b.x);
      const oy = NODE_HEIGHT - Math.abs(a.y - b.y);
      if (ox > 0 && oy > 0) worst = Math.max(worst, Math.min(ox, oy));
    }
  }
  return worst;
}

/** A ~120 component graph shaped like the large marketplace fixture. */
function bigGraph() {
  const domains = ["edge", "payments", "orders", "catalog", "search", "users", "fulfillment", "platform"];
  const many: { id: string; domain: string }[] = [];
  const links: { source: string; target: string }[] = [];
  domains.forEach((domain, d) => {
    for (let i = 0; i < 15; i++) {
      many.push({ id: `${domain}_${i}`, domain });
      if (i > 0) links.push({ source: `${domain}_${Math.floor((i - 1) / 2)}`, target: `${domain}_${i}` });
    }
    if (d > 0) links.push({ source: "edge_0", target: `${domain}_0` });
  });
  return { many, links };
}

describe("layoutForceDirected", () => {
  it("is deterministic for the same graph and seed", () => {
    const a = layoutForceDirected(nodes, edges, { seed: 3 });
    const b = layoutForceDirected(nodes, edges, { seed: 3 });
    expect(a).toEqual(b);
    expect(Object.keys(a).sort()).toEqual(["api", "cache", "client", "db"]);
  });

  it("places connected nodes closer than unconnected ones on average", () => {
    const p = layoutForceDirected(nodes, edges);
    const d = (x: string, y: string) => Math.hypot(p[x]!.x - p[y]!.x, p[x]!.y - p[y]!.y);
    expect(d("client", "api")).toBeLessThan(d("client", "db") + d("client", "cache"));
  });

  it("leaves no overlapping nodes on a large graph", () => {
    const { many, links } = bigGraph();
    const p = layoutForceDirected(many, links);
    expect(Object.keys(p)).toHaveLength(many.length);
    expect(worstOverlap(p)).toBeLessThanOrEqual(1);
    for (const pos of Object.values(p)) {
      expect(Number.isFinite(pos.x) && Number.isFinite(pos.y)).toBe(true);
    }
  });

  it("handles empty graphs and duplicate ids", () => {
    expect(layoutForceDirected([], [])).toEqual({});
    expect(Object.keys(layoutForceDirected([{ id: "a" }, { id: "a" }], []))).toEqual(["a"]);
  });
});

describe("layoutByDomain", () => {
  it("is deterministic and never overlaps nodes", () => {
    const { many, links } = bigGraph();
    const a = layoutByDomain(many, links);
    expect(a).toEqual(layoutByDomain(many, links));
    expect(worstOverlap(a)).toBe(0);
  });

  it("keeps each domain in its own block", () => {
    const { many, links } = bigGraph();
    const p = layoutByDomain(many, links);
    const box = (domain: string) => {
      const ids = many.filter((n) => n.domain === domain).map((n) => p[n.id]!);
      return {
        minX: Math.min(...ids.map((q) => q.x)),
        maxX: Math.max(...ids.map((q) => q.x + NODE_WIDTH)),
        minY: Math.min(...ids.map((q) => q.y)),
        maxY: Math.max(...ids.map((q) => q.y + NODE_HEIGHT)),
      };
    };
    const a = box("payments");
    const b = box("orders");
    const disjoint = a.maxX <= b.minX || b.maxX <= a.minX || a.maxY <= b.minY || b.maxY <= a.minY;
    expect(disjoint).toBe(true);
  });

  it("groups components without a domain together", () => {
    const p = layoutByDomain([{ id: "a" }, { id: "b", domain: "x" }], []);
    expect(Object.keys(p).sort()).toEqual(["a", "b"]);
  });
});

describe("autoLayout", () => {
  it("dispatches every kind", () => {
    for (const kind of ["hierarchical-tb", "hierarchical-lr", "force", "domain"] as const) {
      expect(Object.keys(autoLayout(kind, nodes, edges)).sort()).toEqual(["api", "cache", "client", "db"]);
    }
  });
});
