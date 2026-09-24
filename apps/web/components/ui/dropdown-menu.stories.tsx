import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Copy, Trash2 } from "lucide-react";

import { Button } from "./button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "./dropdown-menu";

const meta: Meta<typeof DropdownMenuContent> = { title: "UI/DropdownMenu", component: DropdownMenuContent };
export default meta;

type Story = StoryObj<typeof DropdownMenuContent>;

export const Actions: Story = {
  render: () => (
    <DropdownMenu defaultOpen modal={false}>
      <DropdownMenuTrigger asChild>
        <Button>Component actions</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuLabel>PostgreSQL</DropdownMenuLabel>
        <DropdownMenuItem shortcut="mod+d">
          <Copy aria-hidden />
          Duplicate
        </DropdownMenuItem>
        <DropdownMenuItem disabled>Replace (select a component first)</DropdownMenuItem>
        <DropdownMenuSeparator />
        <DropdownMenuItem shortcut="delete">
          <Trash2 aria-hidden />
          Delete
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};

export const RadioGroup: Story = {
  render: () => (
    <DropdownMenu defaultOpen modal={false}>
      <DropdownMenuTrigger asChild>
        <Button>Analysis mode</Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="start">
        <DropdownMenuRadioGroup value="capacity">
          <DropdownMenuRadioItem value="none">None</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="capacity">Capacity</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="reliability">Reliability</DropdownMenuRadioItem>
          <DropdownMenuRadioItem value="cost">Cost</DropdownMenuRadioItem>
        </DropdownMenuRadioGroup>
      </DropdownMenuContent>
    </DropdownMenu>
  ),
};
