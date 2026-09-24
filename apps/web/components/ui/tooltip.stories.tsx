import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "./button";
import { Tooltip } from "./tooltip";

const meta: Meta<typeof Tooltip> = { title: "UI/Tooltip", component: Tooltip };
export default meta;

type Story = StoryObj<typeof Tooltip>;

export const WithShortcut: Story = {
  render: () => (
    <div className="flex h-24 items-start gap-4">
      <Tooltip content="Search commands" shortcut="mod+k">
        <Button>Hover or focus me</Button>
      </Tooltip>
      <Tooltip content="Only healthy components" side="right">
        <Button variant="ghost">Plain tooltip</Button>
      </Tooltip>
    </div>
  ),
};
