import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Badge } from "./badge";
import { Card, CardContent, CardHeader, CardTitle } from "./card";

const meta: Meta<typeof Card> = { title: "UI/Card", component: Card };
export default meta;

type Story = StoryObj<typeof Card>;

export const WithHeader: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardHeader>
        <CardTitle>Next bottleneck</CardTitle>
        <Badge tone="info">Calculated</Badge>
      </CardHeader>
      <CardContent className="text-sm text-fg-secondary">
        PostgreSQL write connections reach 100% at about 7.8M daily active users.
      </CardContent>
    </Card>
  ),
};

export const Plain: Story = {
  render: () => (
    <Card className="max-w-md">
      <CardContent className="text-sm">A card without a header.</CardContent>
    </Card>
  ),
};
