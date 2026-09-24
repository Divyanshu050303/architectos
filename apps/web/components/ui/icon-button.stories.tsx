import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Grid3x3, Maximize2, Redo2, Undo2 } from "lucide-react";

import { IconButton } from "./icon-button";

const meta: Meta<typeof IconButton> = {
  title: "UI/IconButton",
  component: IconButton,
  args: { label: "Fit architecture", shortcut: "f", children: <Maximize2 aria-hidden /> },
};
export default meta;

type Story = StoryObj<typeof IconButton>;

export const Default: Story = {};
export const Active: Story = {
  args: { label: "Grid", shortcut: undefined, active: true, children: <Grid3x3 aria-hidden /> },
};
export const Small: Story = { args: { size: "sm" } };
export const Disabled: Story = {
  args: { label: "Undo", shortcut: "mod+z", disabled: true, children: <Undo2 aria-hidden /> },
};

export const Toolbar: Story = {
  render: () => (
    <div className="flex items-center gap-1 rounded-md border border-default bg-surface p-1">
      <IconButton label="Undo" shortcut="mod+z">
        <Undo2 aria-hidden />
      </IconButton>
      <IconButton label="Redo" shortcut="mod+shift+z" disabled>
        <Redo2 aria-hidden />
      </IconButton>
      <IconButton label="Fit architecture" shortcut="f">
        <Maximize2 aria-hidden />
      </IconButton>
      <IconButton label="Grid" active>
        <Grid3x3 aria-hidden />
      </IconButton>
    </div>
  ),
};
