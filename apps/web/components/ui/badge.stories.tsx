import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Badge, StatusBadge } from "./badge";
import { Meter } from "./progress";

const meta: Meta<typeof Badge> = { title: "UI/Badge", component: Badge };
export default meta;

type Story = StoryObj<typeof Badge>;

export const Tones: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <Badge>neutral</Badge>
      <Badge tone="accent">accent</Badge>
      <Badge tone="warning">warning</Badge>
      <Badge tone="danger">danger</Badge>
      <Badge tone="info">calculated</Badge>
    </div>
  ),
};

export const Status: Story = {
  render: () => (
    <div className="flex flex-wrap gap-2">
      <StatusBadge status="healthy" />
      <StatusBadge status="warning" />
      <StatusBadge status="critical" />
      <StatusBadge status="unknown" />
    </div>
  ),
};

export const Meters: Story = {
  render: () => (
    <div className="flex w-72 flex-col gap-3">
      <Meter label="API CPU" value={0.61} threshold={0.7} />
      <Meter label="PostgreSQL connections" value={0.82} threshold={0.7} tone="warning" />
      <Meter label="Kafka throughput" value={0.31} tone="info" />
    </div>
  ),
};
