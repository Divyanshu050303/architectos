import type { Meta, StoryObj } from "@storybook/nextjs-vite";
import { useState } from "react";

import { Combobox, type ComboboxOption } from "./combobox";
import { Field } from "./input";

const SCENARIOS: ComboboxOption[] = [
  { value: "pg", label: "PostgreSQL failure", description: "The primary becomes unreachable." },
  { value: "redis", label: "Redis failure", description: "Redis restarts with an empty cache." },
  { value: "kafka", label: "Kafka outage", description: "Kafka stops accepting writes." },
  { value: "region", label: "Region failure", description: "Everything in one region goes down." },
  { value: "partition", label: "Network partition", description: "Services lose each other." },
  { value: "spike", label: "Traffic spike", description: "Ten times the usual traffic." },
  {
    value: "legacy",
    label: "Legacy batch job",
    description: "Not available for this architecture.",
    disabled: true,
  },
];

function ScenarioPicker({ initial = "pg", disabled = false }: { initial?: string; disabled?: boolean }) {
  const [value, setValue] = useState(initial);
  const selected = SCENARIOS.find((s) => s.value === value);
  return (
    <div className="h-80 max-w-xs">
      <Field label="Scenario" description={selected?.description}>
        {({ id, describedBy }) => (
          <Combobox
            id={id}
            aria-describedby={describedBy}
            options={SCENARIOS}
            value={value}
            onValueChange={setValue}
            listLabel="Scenarios"
            placeholder="Search scenarios"
            disabled={disabled}
          />
        )}
      </Field>
    </div>
  );
}

const meta: Meta<typeof Combobox> = { title: "UI/Combobox", component: Combobox };
export default meta;

type Story = StoryObj<typeof Combobox>;

export const Searchable: Story = { render: () => <ScenarioPicker /> };
export const NothingSelected: Story = { render: () => <ScenarioPicker initial="" /> };
export const Disabled: Story = { render: () => <ScenarioPicker disabled /> };
