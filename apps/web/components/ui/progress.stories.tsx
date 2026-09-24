import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Meter } from "./progress";

const meta: Meta<typeof Meter> = {
  title: "UI/Meter",
  component: Meter,
  args: { label: "API CPU", value: 0.61, threshold: 0.7 },
  decorators: [(Story) => <div className="w-72">{Story()}</div>],
};
export default meta;

type Story = StoryObj<typeof Meter>;

export const Healthy: Story = {};
export const Warning: Story = { args: { label: "PostgreSQL connections", value: 0.82, tone: "warning" } };
export const Critical: Story = { args: { label: "Kafka partitions", value: 1.2, tone: "danger" } };
export const Info: Story = {
  args: { label: "Redis memory", value: 0.31, tone: "info", threshold: undefined },
};
export const Neutral: Story = {
  args: { label: "Not analyzed", value: 0, tone: "neutral", threshold: undefined },
};
