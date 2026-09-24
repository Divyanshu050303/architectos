import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Grid3x3, Maximize2, Plus, Sparkle } from "lucide-react";

import { Button } from "./button";
import { IconButton } from "./icon-button";

const meta: Meta<typeof Button> = {
  title: "UI/Button",
  component: Button,
  args: { children: "Run validation" },
};
export default meta;

type Story = StoryObj<typeof Button>;

export const Primary: Story = { args: { variant: "primary" } };
export const Secondary: Story = { args: { variant: "secondary" } };
export const Ghost: Story = { args: { variant: "ghost" } };
export const Danger: Story = { args: { variant: "danger", children: "Delete component" } };
export const Loading: Story = { args: { variant: "primary", loading: true, children: "Saving" } };

export const AllVariants: Story = {
  render: () => (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2">
        <Button variant="primary">Apply</Button>
        <Button variant="secondary">Edit</Button>
        <Button variant="ghost">Reject</Button>
        <Button variant="danger">Delete</Button>
        <Button variant="ai">
          <Sparkle aria-hidden className="size-3.5" />
          Review change
        </Button>
        <Button variant="primary" disabled>
          Disabled
        </Button>
      </div>
      <div className="flex items-center gap-2">
        <Button size="sm" variant="secondary">
          <Plus aria-hidden className="size-3.5" />
          Add component
        </Button>
        <IconButton label="Fit architecture" shortcut="f">
          <Maximize2 aria-hidden />
        </IconButton>
        <IconButton label="Grid" active>
          <Grid3x3 aria-hidden />
        </IconButton>
      </div>
    </div>
  ),
};
