import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useId, useState } from "react";

import { Slider } from "./slider";

const meta: Meta<typeof Slider> = { title: "UI/Slider", component: Slider };
export default meta;

type Story = StoryObj<typeof Slider>;

function LabelledSlider() {
  const id = useId();
  const [replicas, setReplicas] = useState(3);
  return (
    <div className="flex max-w-xs flex-col gap-1.5">
      <div className="flex items-baseline justify-between">
        <span id={id} className="text-xs font-medium text-fg-secondary">
          API replicas
        </span>
        <span className="tabular text-sm font-semibold text-fg">{replicas}</span>
      </div>
      <Slider aria-labelledby={id} min={1} max={12} value={replicas} onValueChange={setReplicas} />
    </div>
  );
}

export const Labelled: Story = { render: () => <LabelledSlider /> };

export const DangerTone: Story = {
  render: () => (
    <div className="max-w-xs">
      <Slider aria-label="Simulation timeline" tone="danger" min={0} max={5} defaultValue={2} />
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="max-w-xs">
      <Slider aria-label="Peak RPS" disabled defaultValue={40} />
    </div>
  ),
};
