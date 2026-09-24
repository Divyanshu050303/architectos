import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { StatusBadge } from "./badge";
import { Table, Td, Th } from "./table";

const meta: Meta<typeof Table> = { title: "UI/Table", component: Table };
export default meta;

type Story = StoryObj<typeof Table>;

const ROWS = [
  { name: "API", resource: "CPU", value: "61%", status: "healthy" as const },
  { name: "PostgreSQL", resource: "Connections", value: "82%", status: "warning" as const },
  { name: "Kafka", resource: "Partitions", value: "104%", status: "critical" as const },
];

export const Utilization: Story = {
  render: () => (
    <div className="max-w-2xl">
      <Table>
        <caption className="sr-only">Component utilization</caption>
        <thead>
          <tr>
            <Th>Component</Th>
            <Th>Resource</Th>
            <Th className="text-right">Utilization</Th>
            <Th>Status</Th>
          </tr>
        </thead>
        <tbody>
          {ROWS.map((row) => (
            <tr key={row.name}>
              <Td className="font-medium">{row.name}</Td>
              <Td className="text-fg-secondary">{row.resource}</Td>
              <Td className="tabular text-right">{row.value}</Td>
              <Td>
                <StatusBadge status={row.status} />
              </Td>
            </tr>
          ))}
        </tbody>
      </Table>
    </div>
  ),
};
