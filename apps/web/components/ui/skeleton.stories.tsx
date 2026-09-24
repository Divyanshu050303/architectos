import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Skeleton, SkeletonGroup } from "./skeleton";

const meta: Meta<typeof Skeleton> = { title: "UI/Skeleton", component: Skeleton };
export default meta;

type Story = StoryObj<typeof Skeleton>;

export const Block: Story = { args: { className: "h-24 w-72" } };

export const CapacityPage: Story = {
  render: () => (
    <SkeletonGroup label="Loading capacity analysis" className="flex max-w-3xl flex-col gap-4">
      <div className="grid grid-cols-3 gap-3">
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
        <Skeleton className="h-20" />
      </div>
      <Skeleton className="h-48" />
    </SkeletonGroup>
  ),
};
