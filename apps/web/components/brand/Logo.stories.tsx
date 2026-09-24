import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { LogoMark, Wordmark } from "./Logo";

const meta: Meta<typeof Wordmark> = { title: "Brand/Logo", component: Wordmark };
export default meta;

type Story = StoryObj<typeof Wordmark>;

export const WordmarkDefault: Story = { args: { className: "text-sm" } };
export const WithTagline: Story = { args: { className: "text-2xl", size: 48, tagline: true } };

export const MarkSizes: Story = {
  render: () => (
    <div className="flex items-end gap-6">
      {[16, 20, 32, 48, 96].map((size) => (
        <div key={size} className="flex flex-col items-center gap-2">
          <LogoMark size={size} />
          <span className="tabular text-2xs text-muted">{size}px</span>
        </div>
      ))}
    </div>
  ),
};
