import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { ReactFlow, ReactFlowProvider } from "@xyflow/react";

import type { ArchitectureNode as DomainNode } from "@/types/architecture";
import type { ComponentType, NodeVisualState } from "@/types/component";

import { COMPONENT_TYPE_META } from "../constants";
import type { CanvasFlowNode, CanvasNodeData } from "../hooks/useArchitectureCanvas";
import type { NodeBadge } from "../utils/node-transform";
import { ArchitectureNode, ArchitectureNodeCard } from "./ArchitectureNode";

const LABELS: Record<NodeVisualState, string> = {
  default: "Not analyzed",
  healthy: "Healthy",
  warning: "Warning",
  critical: "Critical",
  disabled: "Disabled",
  loading: "Analyzing",
  simulating: "Simulating",
};

function nodeData(
  overrides: Partial<
    Pick<
      CanvasNodeData,
      "status" | "metric" | "utilization" | "badges" | "highlighted" | "dimmed" | "mode" | "preview"
    >
  > & {
    type?: ComponentType;
    name?: string;
    technology?: string;
    description?: string;
  } = {},
): CanvasNodeData {
  const {
    type = "database",
    name = "PostgreSQL",
    technology = "PostgreSQL 16",
    description,
    ...rest
  } = overrides;
  const node: DomainNode = {
    id: `${type}_${name.toLowerCase().replace(/\W+/g, "_")}`,
    type,
    name,
    technology,
    description,
    configuration: { replicas: 2 },
    position: { x: 0, y: 0 },
  };
  const status = rest.status ?? "default";
  return {
    node,
    category: COMPONENT_TYPE_META[type].category,
    status,
    statusLabel: LABELS[status],
    metric: null,
    utilization: null,
    badges: [] as NodeBadge[],
    highlighted: false,
    dimmed: false,
    mode: "topology",
    preview: null,
    direction: "LR",
    ...rest,
  };
}

const capacity = (value: number, label = "connections") => ({
  mode: "capacity" as const,
  metric: { label, value: `${Math.round(value * 100)}%` },
  utilization: value,
});

const meta: Meta<typeof ArchitectureNodeCard> = {
  title: "Architecture/Node",
  component: ArchitectureNodeCard,
  args: { data: nodeData(), selected: false },
};
export default meta;

type Story = StoryObj<typeof ArchitectureNodeCard>;

export const Default: Story = {};
export const Healthy: Story = { args: { data: nodeData({ status: "healthy", ...capacity(0.42) }) } };
export const Warning: Story = {
  args: {
    data: nodeData({
      status: "warning",
      ...capacity(0.82),
      badges: [{ label: "Bottleneck", tone: "warning" }],
    }),
  },
};
export const Critical: Story = {
  args: {
    data: nodeData({
      status: "critical",
      ...capacity(1.12),
      badges: [{ label: "Bottleneck", tone: "danger" }],
    }),
  },
};
export const Selected: Story = { args: { data: nodeData({ status: "healthy" }), selected: true } };
export const Highlighted: Story = { args: { data: nodeData({ status: "warning", highlighted: true }) } };
export const Dimmed: Story = { args: { data: nodeData({ dimmed: true }) } };
export const Disabled: Story = { args: { data: nodeData({ status: "disabled" }) } };
export const Loading: Story = { args: { data: nodeData({ status: "loading" }) } };
export const Simulating: Story = {
  args: {
    data: nodeData({ type: "service", name: "Order Service", technology: "Go", status: "simulating" }),
  },
};
export const SpofBadge: Story = {
  name: "Reliability: SPOF",
  args: {
    data: nodeData({
      type: "service",
      name: "Payment Service",
      technology: "Java",
      mode: "reliability",
      status: "critical",
      badges: [{ label: "SPOF", tone: "danger" }],
    }),
  },
};
export const ProposedAddition: Story = {
  args: { data: nodeData({ type: "cache", name: "Redis", technology: "Redis 7", preview: "added" }) },
};
export const ProposedRemoval: Story = {
  args: { data: nodeData({ type: "queue", name: "Kafka", technology: "Kafka", preview: "removed" }) },
};

export const AllStates: Story = {
  render: () => {
    const items: Array<[string, CanvasNodeData, boolean?]> = [
      ["Default", nodeData()],
      ["Healthy · capacity", nodeData({ status: "healthy", ...capacity(0.42) })],
      [
        "Warning · bottleneck",
        nodeData({
          status: "warning",
          ...capacity(0.82),
          badges: [{ label: "Bottleneck", tone: "warning" }],
        }),
      ],
      [
        "Critical · SPOF",
        nodeData({ status: "critical", mode: "reliability", badges: [{ label: "SPOF", tone: "danger" }] }),
      ],
      ["Selected", nodeData({ status: "healthy" }), true],
      ["Highlighted", nodeData({ status: "warning", highlighted: true })],
      ["Dimmed", nodeData({ dimmed: true })],
      ["Loading", nodeData({ status: "loading" })],
      ["Simulating", nodeData({ status: "simulating" })],
      ["Disabled", nodeData({ status: "disabled" })],
      ["✦ Proposed", nodeData({ type: "cache", name: "Redis", technology: "Redis 7", preview: "added" })],
      ["✦ Changed", nodeData({ status: "healthy", preview: "updated" })],
    ];
    return (
      <ul className="grid grid-cols-[repeat(auto-fill,minmax(14rem,1fr))] gap-6">
        {items.map(([label, data, selected]) => (
          <li key={label} className="flex flex-col gap-2">
            <span className="label-caps">{label}</span>
            <ArchitectureNodeCard data={data} selected={selected} />
          </li>
        ))}
      </ul>
    );
  },
};

const FLOW_NODES: CanvasFlowNode[] = [
  {
    id: "api",
    type: "architecture",
    position: { x: 0, y: 40 },
    data: nodeData({
      type: "service",
      name: "API",
      technology: "Node.js",
      status: "healthy",
      ...capacity(0.61, "CPU"),
    }),
  },
  {
    id: "pg",
    type: "architecture",
    position: { x: 300, y: 40 },
    selected: true,
    data: nodeData({
      status: "warning",
      ...capacity(0.82),
      badges: [{ label: "Bottleneck", tone: "warning" }],
    }),
  },
];

/** Inside React Flow, with connection handles, as it appears on the canvas. */
export const OnCanvas: Story = {
  render: () => (
    <ReactFlowProvider>
      <div className="architecture-canvas h-80 w-full rounded-md border border-default">
        <ReactFlow
          nodes={FLOW_NODES}
          edges={[{ id: "e1", source: "api", target: "pg" }]}
          nodeTypes={{ architecture: ArchitectureNode }}
          fitView
          nodesDraggable={false}
          proOptions={{ hideAttribution: true }}
        />
      </div>
    </ReactFlowProvider>
  ),
};
