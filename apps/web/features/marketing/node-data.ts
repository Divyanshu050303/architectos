/**
 * Builds the view model the real ArchitectureNodeCard renders, so marketing pages show
 * the product's actual node component rather than a lookalike.
 */
import { COMPONENT_TYPE_META } from "@/features/architecture/constants";
import type { CanvasNodeData } from "@/features/architecture/hooks/useArchitectureCanvas";
import type { NodeBadge } from "@/features/architecture/utils/node-transform";
import type { ArchitectureNode } from "@/types/architecture";
import type { ComponentType, NodeVisualState } from "@/types/component";

export interface MarketingNodeInput {
  id: string;
  type: ComponentType;
  name: string;
  technology: string;
  description?: string;
  status?: NodeVisualState;
  statusLabel?: string;
  metric?: { label: string; value: string } | null;
  utilization?: number | null;
  badges?: NodeBadge[];
  highlighted?: boolean;
  dimmed?: boolean;
  capacity?: boolean;
  preview?: CanvasNodeData["preview"];
}

const DEFAULT_LABELS: Record<NodeVisualState, string> = {
  default: "Not analyzed",
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  disabled: "Disabled",
  loading: "Analyzing",
  simulating: "Simulating",
};

export function marketingNode(input: MarketingNodeInput): CanvasNodeData {
  const node: ArchitectureNode = {
    id: input.id,
    type: input.type,
    name: input.name,
    technology: input.technology,
    description: input.description,
    configuration: {},
    position: { x: 0, y: 0 },
  };
  const status = input.status ?? "default";
  return {
    node,
    category: COMPONENT_TYPE_META[input.type].category,
    status,
    statusLabel: input.statusLabel ?? DEFAULT_LABELS[status],
    metric: input.metric ?? null,
    utilization: input.utilization ?? null,
    badges: input.badges ?? [],
    highlighted: input.highlighted ?? false,
    dimmed: input.dimmed ?? false,
    mode: input.capacity ? "capacity" : "topology",
    preview: input.preview ?? null,
    direction: "LR",
  };
}
