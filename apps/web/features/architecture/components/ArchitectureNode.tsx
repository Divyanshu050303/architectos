"use client";

/**
 * Architecture node (spec §24–26). Category, icon, name, technology, key metric and
 * status (dot + text, never colour alone). Visual state is exposed as data attributes.
 */
import { Handle, type NodeProps, Position } from "@xyflow/react";
import { memo } from "react";

import { Badge, type BadgeTone } from "@/components/ui/badge";
import { type MeterTone, Meter } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import type { NodeVisualState } from "@/types/component";

import type { CanvasFlowNode, CanvasNodeData } from "../hooks/useArchitectureCanvas";
import { sameNodeData } from "../utils/canvas-equality";
import type { CoverageChip, NodeBadge, NodeBadgeTone } from "../utils/node-transform";
import type { SimulationNodeState } from "../utils/simulation-playback";
import { ComponentIcon } from "./ComponentIcon";

export { sameNodeData };

const STATUS_DOT: Record<NodeVisualState, string> = {
  default: "bg-control",
  healthy: "bg-accent-strong",
  warning: "bg-warning",
  critical: "bg-danger",
  disabled: "bg-control",
  loading: "bg-info motion-safe:animate-pulse",
  simulating: "bg-info motion-safe:animate-pulse",
};

const STATUS_TEXT: Record<NodeVisualState, string> = {
  default: "text-muted",
  healthy: "text-accent-fg",
  warning: "text-warning-fg",
  critical: "text-danger-fg",
  disabled: "text-muted",
  loading: "text-info-fg",
  simulating: "text-info-fg",
};

const STATUS_BORDER: Record<NodeVisualState, string> = {
  default: "border-default hover:border-strong",
  healthy: "border-default hover:border-strong",
  warning: "border-warning/60 hover:border-warning",
  critical: "border-danger/70 hover:border-danger",
  disabled: "border-default",
  loading: "border-default",
  simulating: "border-info",
};

const METER_TONE: Partial<Record<NodeVisualState, MeterTone>> = {
  healthy: "accent",
  warning: "warning",
  critical: "danger",
};

const BADGE_TONE: Record<NodeBadgeTone, BadgeTone> = {
  warning: "warning",
  danger: "danger",
  info: "info",
  accent: "accent",
  neutral: "neutral",
};

/** Simulation playback states (spec §41, §72): subtle, and static under reduced motion. */
const SIMULATION_CLASS: Record<SimulationNodeState, string> = {
  normal: "",
  failed: "border-danger bg-danger-soft ring-2 ring-danger/20",
  impacted: "border-warning bg-warning-soft",
  cascade: "border-dashed border-danger ring-2 ring-danger/15",
};

const PREVIEW_BADGE = {
  added: { label: "✦ Proposed", tone: "accent" },
  removed: { label: "Removed", tone: "danger" },
  updated: { label: "✦ Changed", tone: "accent" },
} as const;

function borderClass(data: CanvasNodeData, selected: boolean): string {
  if (data.preview === "added" || data.preview === "updated") return "border-dashed border-accent-strong";
  if (data.preview === "removed") return "border-dashed border-danger";
  if (selected) return "border-accent-strong";
  if (data.highlighted) return "border-warning";
  if (data.simulation && data.simulation !== "normal") return SIMULATION_CLASS[data.simulation];
  return STATUS_BORDER[data.status];
}

function CoverageChips({ chips }: { chips: readonly CoverageChip[] }) {
  return (
    <span className="flex items-center gap-1" aria-label="Telemetry coverage">
      {chips.map((chip) => (
        <span
          key={chip.key}
          title={`${chip.label} ${chip.present ? "present" : "missing"}`}
          data-present={chip.present || undefined}
          className={cn(
            "tabular inline-flex size-4 items-center justify-center rounded-sm border text-2xs leading-none font-semibold",
            chip.present
              ? "border-info/40 bg-info-soft text-info-fg"
              : "border-dashed border-control text-muted line-through opacity-70",
          )}
        >
          <span aria-hidden>{chip.key}</span>
          <span className="sr-only">
            {chip.label} {chip.present ? "present" : "missing"}
          </span>
        </span>
      ))}
    </span>
  );
}

export interface ArchitectureNodeCardProps {
  data: CanvasNodeData;
  selected?: boolean;
}

/** Pure visual of a node; the React Flow wrapper below adds connection handles. */
export function ArchitectureNodeCard({ data, selected = false }: ArchitectureNodeCardProps) {
  const {
    node,
    category,
    status,
    statusLabel,
    metric,
    utilization,
    badges,
    highlighted,
    dimmed,
    mode,
    preview,
    coverage,
    simulation,
    versionChanged,
  } = data;
  const meterTone = METER_TONE[status] ?? "neutral";

  return (
    <div
      data-status={status}
      data-selected={selected || undefined}
      data-highlighted={highlighted || undefined}
      data-dimmed={dimmed || undefined}
      data-preview={preview ?? undefined}
      data-simulation={simulation ?? undefined}
      data-version-changed={versionChanged || undefined}
      aria-busy={status === "loading" || undefined}
      className={cn(
        "relative flex w-56 flex-col gap-2 rounded-md border bg-surface px-3 py-2.5 text-left shadow-subtle",
        "transition-[border-color,box-shadow,opacity,background-color] duration-300 motion-reduce:transition-none",
        borderClass(data, selected),
        selected && "ring-2 ring-accent/25",
        // Keyboard focus lands on React Flow's wrapper; show the ring on the card.
        "in-focus-visible:ring-2 in-focus-visible:ring-ring",
        highlighted && "arch-node-highlighted",
        versionChanged && "arch-node-version-changed",
        dimmed && "opacity-40",
        status === "disabled" && "opacity-50",
        preview === "added" && "bg-accent-soft",
        preview === "removed" && "opacity-60",
      )}
    >
      <div className="flex min-w-0 items-center gap-1.5">
        <ComponentIcon node={node} className="size-3.5 shrink-0 text-muted" />
        <span className="label-caps truncate">{category}</span>
        <span className="ml-auto flex shrink-0 items-center gap-1">
          {preview ? <Badge tone={PREVIEW_BADGE[preview].tone}>{PREVIEW_BADGE[preview].label}</Badge> : null}
          {badges.map((badge: NodeBadge) => (
            <Badge key={badge.label} tone={BADGE_TONE[badge.tone]}>
              {badge.label}
            </Badge>
          ))}
        </span>
      </div>

      <div className="min-w-0">
        <p className={cn("truncate text-sm font-semibold text-fg", preview === "removed" && "line-through")}>
          {node.name}
        </p>
        <p className="truncate text-xs text-fg-secondary">
          {node.technology}
          {node.description ? <span className="text-muted"> · {node.description}</span> : null}
        </p>
      </div>

      <div className="flex items-center justify-between gap-2 text-xs">
        {coverage ? (
          <CoverageChips chips={coverage} />
        ) : metric ? (
          <span className="tabular truncate text-fg">
            {metric.value}
            {metric.label ? <span className="text-muted"> {metric.label}</span> : null}
          </span>
        ) : (
          <span className="text-muted" aria-hidden>
            —
          </span>
        )}
        <span className={cn("inline-flex shrink-0 items-center gap-1.5 font-medium", STATUS_TEXT[status])}>
          <span aria-hidden className={cn("size-1.5 rounded-full", STATUS_DOT[status])} />
          <span className="sr-only">Status: </span>
          {statusLabel}
        </span>
      </div>

      {mode === "capacity" && utilization !== null ? (
        <Meter
          value={utilization}
          tone={meterTone}
          label={`${node.name} ${metric?.label ?? "utilization"} ${metric?.value ?? ""}`.trim()}
        />
      ) : null}

      {selected ? <span className="sr-only">Selected</span> : null}
    </div>
  );
}

function ArchitectureNodeComponent({ data, selected, isConnectable }: NodeProps<CanvasFlowNode>) {
  const vertical = data.direction === "TB";
  return (
    <>
      <Handle
        type="target"
        position={vertical ? Position.Top : Position.Left}
        isConnectable={isConnectable}
        aria-hidden
      />
      <ArchitectureNodeCard data={data} selected={selected} />
      {/* Handles are a pointer affordance; keyboard users connect via the node toolbar's "Add connection". */}
      <Handle
        type="source"
        position={vertical ? Position.Bottom : Position.Right}
        isConnectable={isConnectable}
        aria-hidden
      />
    </>
  );
}

export const ArchitectureNode = memo(
  ArchitectureNodeComponent,
  (prev, next) =>
    prev.selected === next.selected &&
    prev.dragging === next.dragging &&
    prev.isConnectable === next.isConnectable &&
    sameNodeData(prev.data, next.data),
);
