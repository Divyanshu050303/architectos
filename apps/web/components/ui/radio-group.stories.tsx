import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { Monitor, Moon, Sun } from "lucide-react";

import { RadioGroup } from "./radio-group";

const meta: Meta<typeof RadioGroup> = { title: "UI/RadioGroup", component: RadioGroup };
export default meta;

type Story = StoryObj<typeof RadioGroup>;

export const Default: Story = {
  render: () => (
    <RadioGroup
      label="Consistency"
      defaultValue="strong"
      options={[
        { value: "strong", label: "Strong", description: "Reads always see the latest write." },
        { value: "eventual", label: "Eventual", description: "Reads may lag behind writes." },
        { value: "session", label: "Session", disabled: true },
      ]}
    />
  ),
};

export const Segmented: Story = {
  render: () => (
    <div className="max-w-xs">
      <RadioGroup
        variant="segmented"
        label="Database"
        defaultValue="postgres"
        options={[
          { value: "postgres", label: "Primary only" },
          { value: "postgres-replica", label: "+ Read replica" },
        ]}
      />
    </div>
  ),
};

export const SegmentedWithIcons: Story = {
  render: () => (
    <div className="max-w-sm">
      <RadioGroup
        variant="segmented"
        label="Theme"
        defaultValue="system"
        options={[
          { value: "light", label: "Light", icon: <Sun aria-hidden /> },
          { value: "dark", label: "Dark", icon: <Moon aria-hidden /> },
          { value: "system", label: "System", icon: <Monitor aria-hidden /> },
        ]}
      />
    </div>
  ),
};
