import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Switch } from "./switch";

const meta: Meta<typeof Switch> = { title: "UI/Switch", component: Switch };
export default meta;

type Story = StoryObj<typeof Switch>;

export const WithLabel: Story = {
  render: () => (
    <div className="flex flex-col gap-3 text-sm text-fg">
      <Switch label="Redis cache" defaultChecked />
      <Switch label="Autoscaling" />
      <Switch label="Disabled" disabled />
    </div>
  ),
};

export const LabelledElsewhere: Story = {
  render: () => (
    <div className="flex items-center justify-between gap-4 text-sm">
      <span id="story-switch-label" className="text-fg-secondary">
        Read replica
      </span>
      <Switch aria-labelledby="story-switch-label" />
    </div>
  ),
};
