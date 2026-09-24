import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Field, Input, Select, Textarea } from "./input";

const meta: Meta<typeof Input> = { title: "UI/Input", component: Input };
export default meta;

type Story = StoryObj<typeof Input>;

export const TextInput: Story = {
  render: () => (
    <div className="max-w-sm">
      <Field label="System name" description="Shown in the dashboard and reports.">
        {({ id, describedBy }) => (
          <Input id={id} aria-describedby={describedBy} defaultValue="Food Delivery" />
        )}
      </Field>
    </div>
  ),
};

export const WithError: Story = {
  render: () => (
    <div className="max-w-sm">
      <Field label="Daily active users" error="Enter a whole number greater than 0.">
        {({ id, describedBy, invalid }) => (
          <Input
            id={id}
            aria-describedby={describedBy}
            aria-invalid={invalid}
            inputMode="numeric"
            defaultValue="-5"
          />
        )}
      </Field>
    </div>
  ),
};

export const TextareaField: Story = {
  render: () => (
    <div className="max-w-md">
      <Field label="Description">
        {({ id }) => (
          <Textarea
            id={id}
            defaultValue="Food delivery app with restaurants, riders and customers. Read-heavy menus, bursty orders."
          />
        )}
      </Field>
    </div>
  ),
};

export const SelectField: Story = {
  render: () => (
    <div className="max-w-xs">
      <Field label="Traffic">
        {({ id }) => (
          <Select
            id={id}
            defaultValue="peak"
            options={[
              { value: "current", label: "Current" },
              { value: "peak", label: "Peak" },
              { value: "2x", label: "2×" },
              { value: "10x", label: "10×", disabled: true },
            ]}
          />
        )}
      </Field>
    </div>
  ),
};

export const Disabled: Story = {
  render: () => (
    <div className="max-w-sm">
      <Field label="Project ID">{({ id }) => <Input id={id} disabled defaultValue="proj_food" />}</Field>
    </div>
  ),
};

export const BareTextarea: Story = {
  render: () => (
    <div className="flex max-w-md items-end gap-2 rounded-md border border-strong bg-surface px-3 py-2 focus-within:border-accent-strong focus-within:ring-2 focus-within:ring-accent/20">
      <Textarea
        variant="bare"
        aria-label="Ask ArchitectOS"
        rows={1}
        placeholder="Add a read replica to PostgreSQL"
        className="field-sizing-content max-h-32 min-h-7 flex-1 resize-none py-1"
      />
    </div>
  ),
};
