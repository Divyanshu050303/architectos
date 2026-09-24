import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useState } from "react";
import { fn } from "storybook/test";

import type { ArchitectureNode } from "@/types/architecture";
import type { ComponentUtilization } from "@/types/capacity";
import type { Finding } from "@/types/validation";

import { NodeInspector, type NodeInspectorProps } from "./NodeInspector";

const postgres: ArchitectureNode = {
  id: "postgres",
  type: "database",
  name: "PostgreSQL",
  technology: "PostgreSQL 16",
  description: "Primary store",
  domain: "Orders",
  configuration: { replicas: 1, instance: "db.r6g.xlarge", max_connections: 500, multi_az: false },
  position: { x: 600, y: 120 },
};
const api: ArchitectureNode = {
  id: "api",
  type: "service",
  name: "API",
  technology: "Node.js",
  configuration: { replicas: 3 },
  position: { x: 300, y: 120 },
};
const orders: ArchitectureNode = {
  id: "order_service",
  type: "service",
  name: "Order Service",
  technology: "Go",
  configuration: { replicas: 4 },
  position: { x: 300, y: 280 },
};

const UTILIZATION: ComponentUtilization[] = [
  {
    nodeId: "postgres",
    resource: "Connections",
    used: 410,
    limit: 500,
    unit: "conn",
    utilization: 0.82,
    threshold: 0.7,
    status: "warning",
    evidenceId: "ev_pg_connections",
  },
  {
    nodeId: "postgres",
    resource: "Writes",
    used: 4300,
    limit: 8000,
    unit: "/s",
    utilization: 0.54,
    threshold: 0.7,
    status: "healthy",
    evidenceId: "ev_pg_writes",
  },
];

const FINDINGS: Finding[] = [
  {
    id: "f_pg_spof",
    ruleId: "reliability.single_point_of_failure",
    category: "reliability",
    severity: "critical",
    title: "PostgreSQL is a single point of failure",
    location: "PostgreSQL",
    whyItMatters: "One instance failing takes the API and Order Service down.",
    recommendation: "Add a synchronous standby in a second availability zone.",
    nodeIds: ["postgres"],
    edgeIds: [],
    evidenceIds: ["ev_pg_spof"],
    fixable: true,
    status: "open",
  },
];

function Harness(props: Partial<NodeInspectorProps> & { initialTab?: string }) {
  const { initialTab = "overview", ...rest } = props;
  const [tab, setTab] = useState(initialTab);
  return (
    <div className="flex h-[560px] w-80 flex-col rounded-md border border-default bg-surface">
      <NodeInspector
        projectId="proj_food"
        node={postgres}
        architecture={{
          nodes: [api, orders, postgres],
          edges: [
            { id: "e_api_pg", source: "api", target: "postgres", synchronous: true, critical: true },
            {
              id: "e_orders_pg",
              source: "order_service",
              target: "postgres",
              synchronous: true,
              critical: true,
            },
          ],
        }}
        utilization={UTILIZATION}
        findings={FINDINGS}
        status="critical"
        statusLabel="Critical"
        tab={tab}
        onTabChange={setTab}
        editable
        onCommand={fn()}
        onOpenEvidence={fn()}
        onSelectNode={fn()}
        {...rest}
      />
    </div>
  );
}

const meta: Meta<typeof Harness> = {
  title: "Architecture/Inspector",
  component: Harness,
  parameters: { nextjs: { appDirectory: true, navigation: { pathname: "/project/proj_food/architecture" } } },
};
export default meta;

type Story = StoryObj<typeof Harness>;

export const Overview: Story = {};
export const Configuration: Story = { args: { initialTab: "configuration" } };
export const Capacity: Story = { args: { initialTab: "capacity" } };
export const Constraints: Story = { args: { initialTab: "constraints" } };
export const FailureModes: Story = { args: { initialTab: "failure-modes" } };
export const Evidence: Story = { args: { initialTab: "evidence" } };
export const ReadOnly: Story = { args: { editable: false, initialTab: "configuration" } };
export const NotAnalyzed: Story = {
  name: "Not analyzed (overview only)",
  args: { node: api, utilization: [], findings: [], status: "default", statusLabel: "Not analyzed" },
};
