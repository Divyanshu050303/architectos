import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "./button";
import { Drawer, DrawerContent, DrawerTrigger } from "./drawer";

const meta: Meta<typeof DrawerContent> = { title: "UI/Drawer", component: DrawerContent };
export default meta;

type Story = StoryObj<typeof DrawerContent>;

export const Right: Story = {
  render: () => (
    <Drawer defaultOpen>
      <DrawerTrigger asChild>
        <Button>Open drawer</Button>
      </DrawerTrigger>
      <DrawerContent title="Component settings" description="PostgreSQL · primary">
        <p className="text-sm text-fg-secondary">Drawer body content.</p>
      </DrawerContent>
    </Drawer>
  ),
};

export const Evidence: Story = {
  render: () => (
    <Drawer defaultOpen>
      <DrawerTrigger asChild>
        <Button>Open evidence</Button>
      </DrawerTrigger>
      <DrawerContent title="Evidence" description="Why PostgreSQL is the next bottleneck" tone="evidence">
        <ul className="flex flex-col gap-2 text-sm text-fg-secondary">
          <li>Peak writes: 1,200/s</li>
          <li>Max connections: 500</li>
          <li>Formula: connections = writes × avg latency</li>
        </ul>
      </DrawerContent>
    </Drawer>
  ),
};

export const Bottom: Story = {
  render: () => (
    <Drawer defaultOpen>
      <DrawerTrigger asChild>
        <Button>Open sheet</Button>
      </DrawerTrigger>
      <DrawerContent title="Inspector" side="bottom">
        <p className="text-sm text-fg-secondary">Bottom sheet used at narrow widths.</p>
      </DrawerContent>
    </Drawer>
  ),
};
