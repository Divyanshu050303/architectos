import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "./tabs";

const meta: Meta<typeof Tabs> = { title: "UI/Tabs", component: Tabs };
export default meta;

type Story = StoryObj<typeof Tabs>;

const TABS = ["Overview", "Capacity", "Reliability", "Cost", "Evidence"] as const;

export const Inspector: Story = {
  render: () => (
    <Tabs defaultValue="Capacity" className="max-w-md rounded-md border border-default bg-surface">
      <TabsList aria-label="Component details">
        {TABS.map((tab) => (
          <TabsTrigger key={tab} value={tab}>
            {tab}
          </TabsTrigger>
        ))}
      </TabsList>
      {TABS.map((tab) => (
        <TabsContent key={tab} value={tab} className="p-4 text-sm text-fg-secondary">
          {tab} details for PostgreSQL.
        </TabsContent>
      ))}
    </Tabs>
  ),
};
