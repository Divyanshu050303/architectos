"use client";

/**
 * Collapsed domain in the Overview (spec §65): name, component count, worst status of
 * its members and a short composition. Activating it expands the domain.
 */
import { Handle, type NodeProps, Position } from "@xyflow/react";
import { ChevronsUpDown, Layers } from "lucide-react";
import { memo } from "react";

import { cn } from "@/lib/utils";

import type { DomainFlowNode, DomainNodeData } from "../hooks/useArchitectureCanvas";
import { sameRecord } from "../utils/canvas-equality";
import type { AnalyzedStatus } from "../utils/node-transform";

const STATUS_DOT: Record<AnalyzedStatus, string> = {
  default: "bg-control",
  healthy: "bg-accent-strong",
  warning: "bg-warning",
  critical: "bg-danger",
};

const STATUS_TEXT: Record<AnalyzedStatus, string> = {
  default: "text-muted",
  healthy: "text-accent-fg",
  warning: "text-warning-fg",
  critical: "text-danger-fg",
};

const STATUS_BORDER: Record<AnalyzedStatus, string> = {
  default: "border-strong",
  healthy: "border-strong",
  warning: "border-warning/60",
  critical: "border-danger/70",
};

export function DomainNodeCard({ data }: { data: DomainNodeData }) {
  return (
    <div
      data-status={data.status}
      className={cn(
        "group flex h-[124px] w-[272px] cursor-pointer flex-col justify-between rounded-lg border-2 bg-surface-2 px-4 py-3 text-left shadow-subtle",
        "transition-colors duration-150 motion-reduce:transition-none hover:bg-surface",
        "in-focus-visible:ring-2 in-focus-visible:ring-ring",
        STATUS_BORDER[data.status],
      )}
    >
      <div className="flex items-center gap-1.5">
        <Layers aria-hidden className="size-3.5 text-muted" />
        <span className="label-caps">Domain</span>
        <span className="ml-auto inline-flex items-center gap-1 text-2xs text-muted group-hover:text-fg">
          <ChevronsUpDown aria-hidden className="size-3" />
          Expand
        </span>
      </div>
      <div className="min-w-0">
        <p className="truncate text-lg font-semibold text-fg">{data.label}</p>
        <p className="truncate text-xs text-fg-secondary">{data.typeSummary}</p>
      </div>
      <div className="flex items-center justify-between text-sm">
        <span className="tabular text-fg">
          {data.count} <span className="text-muted">{data.count === 1 ? "component" : "components"}</span>
        </span>
        <span className={cn("inline-flex items-center gap-1.5 font-medium", STATUS_TEXT[data.status])}>
          <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[data.status])} />
          <span className="sr-only">Status: </span>
          {data.statusLabel}
        </span>
      </div>
    </div>
  );
}

function DomainNodeComponent({ data }: NodeProps<DomainFlowNode>) {
  return (
    <>
      <Handle type="target" position={Position.Left} isConnectable={false} aria-hidden />
      <DomainNodeCard data={data} />
      <Handle type="source" position={Position.Right} isConnectable={false} aria-hidden />
    </>
  );
}

export const DomainNode = memo(DomainNodeComponent, (prev, next) => sameRecord(prev.data, next.data));
