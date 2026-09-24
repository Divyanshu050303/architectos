import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { MetricCard } from "./MetricCard";

const meta: Meta<typeof MetricCard> = {
  title: "Capacity/MetricCard",
  component: MetricCard,
  // MetricCard renders dt/dd, so every story places it inside a <dl>.
  decorators: [
    (Story) => (
      <dl className="max-w-60">
        <Story />
      </dl>
    ),
  ],
  args: { label: "Daily active users", value: "2.4M" },
};
export default meta;

type Story = StoryObj<typeof MetricCard>;

export const Default: Story = {};
export const WithUnit: Story = { args: { label: "Peak throughput", value: "31K", unit: "req/s" } };
export const WithDescription: Story = {
  args: {
    label: "Writes",
    value: "8.2K",
    unit: "/s",
    description: "10% of peak requests are writes.",
  },
};

export const CurrentLoad: Story = {
  decorators: [
    (Story) => (
      <div className="max-w-3xl">
        <Story />
      </div>
    ),
  ],
  render: () => (
    <dl className="grid grid-cols-1 gap-3 sm:grid-cols-3">
      <MetricCard label="DAU" value="2.4M" />
      <MetricCard label="Peak RPS" value="31K" unit="req/s" />
      <MetricCard label="Writes" value="8.2K" unit="/s" description="From the requirements read/write mix." />
    </dl>
  ),
};
