import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Checkbox } from "./checkbox";
import { Field } from "./input";

const meta: Meta<typeof Checkbox> = { title: "UI/Checkbox", component: Checkbox };
export default meta;

type Story = StoryObj<typeof Checkbox>;

export const WithLabel: Story = {
  render: () => (
    <div className="flex flex-col gap-3 text-sm text-fg">
      <Checkbox label="Show ignored findings" defaultChecked />
      <Checkbox label="Include read replicas" />
      <Checkbox label="Partially selected" checked="indeterminate" />
      <Checkbox label="Disabled" disabled />
    </div>
  ),
};

export const Small: Story = {
  render: () => <Checkbox size="sm" label="Show ignored (3)" labelClassName="text-xs text-fg-secondary" />,
};

export const InField: Story = {
  render: () => (
    <Field label="Multi-AZ" description="Replicate across availability zones.">
      {({ id, describedBy }) => <Checkbox id={id} aria-describedby={describedBy} defaultChecked />}
    </Field>
  ),
};
