"use client";

/**
 * Trust boundary backdrop for the security overlay (spec §68). Visual only: sized from
 * its members' bounds, never selectable, draggable or connectable.
 */
import type { NodeProps } from "@xyflow/react";
import { memo } from "react";

import { cn } from "@/lib/utils";

import type { BoundaryFlowNode } from "../hooks/useArchitectureCanvas";
import type { LabelAnchor } from "../utils/overlay-geometry";

const LABEL_POSITION: Record<LabelAnchor, string> = {
  "top-left": "top-1.5 left-2.5",
  "top-right": "top-1.5 right-2.5",
  "bottom-left": "bottom-1 left-2.5",
  "bottom-right": "bottom-1 right-2.5",
};

function BoundaryNodeComponent({ data, width, height }: NodeProps<BoundaryFlowNode>) {
  return (
    <div
      aria-hidden
      style={{ width, height }}
      className="pointer-events-none rounded-lg border border-dashed border-info/50 bg-info-soft/30"
    >
      <span
        className={cn(
          "absolute rounded-sm bg-surface/80 px-1 text-2xs font-semibold tracking-wide text-info-fg uppercase",
          LABEL_POSITION[data.labelAnchor],
        )}
      >
        {data.name}
      </span>
    </div>
  );
}

export const BoundaryNode = memo(BoundaryNodeComponent);
