/**
 * Trust boundary backdrops for the security overlay (spec §68). Visual only: each
 * rectangle is the padded bounding box of the boundary's member components.
 */
import type { Position } from "@/types/architecture";
import type { TrustBoundary } from "@/types/security";

import { NODE_HEIGHT, NODE_WIDTH } from "../constants";

export type LabelAnchor = "top-left" | "top-right" | "bottom-left" | "bottom-right";

export interface BoundaryRect {
  id: string;
  name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  /** Corner for the name, chosen so names of overlapping boundaries do not collide. */
  labelAnchor: LabelAnchor;
}

// Bottom first: in top-down graphs, labels of connections entering a boundary sit near its top edge.
const ANCHORS: readonly LabelAnchor[] = ["bottom-left", "bottom-right", "top-left", "top-right"];
const LABEL_HEIGHT = 18;

/** Approximate box of an uppercase 2xs label, in flow coordinates. */
function labelBox(rect: Omit<BoundaryRect, "labelAnchor">, anchor: LabelAnchor) {
  const width = rect.name.length * 7 + 20;
  const x = anchor.endsWith("left") ? rect.x : rect.x + rect.width - width;
  const y = anchor.startsWith("top") ? rect.y : rect.y + rect.height - LABEL_HEIGHT;
  return { x, y, width, height: LABEL_HEIGHT };
}

type Box = { x: number; y: number; width: number; height: number };

function intersects(a: Box, b: Box): boolean {
  return a.x < b.x + b.width && b.x < a.x + a.width && a.y < b.y + b.height && b.y < a.y + a.height;
}

const PADDING = 20;
/** Room above the members for the boundary's name. */
const LABEL_SPACE = 22;

export function boundaryRects(
  boundaries: readonly TrustBoundary[],
  positions: ReadonlyMap<string, Position>,
  sizes?: ReadonlyMap<string, { width: number; height: number }>,
): BoundaryRect[] {
  const rects: BoundaryRect[] = [];
  const placed: Box[] = [];
  for (const boundary of boundaries) {
    let minX = Number.POSITIVE_INFINITY;
    let minY = Number.POSITIVE_INFINITY;
    let maxX = Number.NEGATIVE_INFINITY;
    let maxY = Number.NEGATIVE_INFINITY;
    for (const nodeId of boundary.nodeIds) {
      const p = positions.get(nodeId);
      if (!p) continue;
      const size = sizes?.get(nodeId);
      minX = Math.min(minX, p.x);
      minY = Math.min(minY, p.y);
      maxX = Math.max(maxX, p.x + (size?.width ?? NODE_WIDTH));
      maxY = Math.max(maxY, p.y + (size?.height ?? NODE_HEIGHT));
    }
    if (!Number.isFinite(minX)) continue;
    const rect = {
      id: boundary.id,
      name: boundary.name,
      x: minX - PADDING,
      y: minY - PADDING - LABEL_SPACE,
      width: maxX - minX + PADDING * 2,
      height: maxY - minY + PADDING * 2 + LABEL_SPACE,
    };
    const labelAnchor =
      ANCHORS.find((anchor) => !placed.some((box) => intersects(box, labelBox(rect, anchor)))) ?? "top-left";
    placed.push(labelBox(rect, labelAnchor));
    rects.push({ ...rect, labelAnchor });
  }
  return rects;
}
