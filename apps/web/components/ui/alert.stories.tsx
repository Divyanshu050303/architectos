import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Alert } from "./alert";
import { Button } from "./button";

const meta: Meta<typeof Alert> = {
  title: "UI/Alert",
  component: Alert,
  args: { tone: "info", title: "Capacity analysis is based on v3." },
};
export default meta;

type Story = StoryObj<typeof Alert>;

export const Info: Story = {};
export const Success: Story = { args: { tone: "success", title: "Validation passed with no findings." } };
export const Warning: Story = {
  args: {
    tone: "warning",
    title: "These results are for v2; the architecture is now v3.",
    children: "Numbers below may not reflect the latest changes.",
    actions: (
      <Button size="sm" variant="secondary">
        Re-run for v3
      </Button>
    ),
  },
};
export const Danger: Story = {
  args: {
    tone: "danger",
    title: "The architecture could not be saved.",
    children: "Your changes are kept locally. No changes were applied on the server.",
  },
};

export const AllTones: Story = {
  render: () => (
    <div className="flex max-w-xl flex-col gap-3">
      <Alert tone="info" title="Info: calculated by the capacity engine." />
      <Alert tone="success" title="Success: architecture v4 saved." />
      <Alert tone="warning" title="Warning: PostgreSQL connections at 82%." />
      <Alert tone="danger" title="Critical: single point of failure in the write path." />
    </div>
  ),
};
