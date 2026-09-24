import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "./button";
import { Popover, PopoverContent, PopoverTrigger } from "./popover";

const meta: Meta<typeof PopoverContent> = { title: "UI/Popover", component: PopoverContent };
export default meta;

type Story = StoryObj<typeof PopoverContent>;

export const Default: Story = {
  render: () => (
    <div className="h-48">
      <Popover defaultOpen>
        <PopoverTrigger asChild>
          <Button>How is this calculated?</Button>
        </PopoverTrigger>
        <PopoverContent align="start" className="w-72">
          <p className="font-medium text-fg">Peak RPS</p>
          <p className="mt-1 text-xs text-fg-secondary">
            Daily active users × requests per user ÷ 86,400 × peak factor, from your requirements.
          </p>
        </PopoverContent>
      </Popover>
    </div>
  ),
};
