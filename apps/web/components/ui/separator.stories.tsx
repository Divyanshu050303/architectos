import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Separator } from "./separator";

const meta: Meta<typeof Separator> = { title: "UI/Separator", component: Separator };
export default meta;

type Story = StoryObj<typeof Separator>;

export const Horizontal: Story = {
  render: () => (
    <div className="flex max-w-sm flex-col gap-3 text-sm">
      <span>Requirements</span>
      <Separator />
      <span>Architecture</span>
    </div>
  ),
};

export const Vertical: Story = {
  render: () => (
    <div className="flex h-6 items-center gap-3 text-sm">
      <span>v3</span>
      <Separator orientation="vertical" />
      <span>Saved</span>
      <Separator orientation="vertical" decorative={false} />
      <span>Mock data</span>
    </div>
  ),
};
