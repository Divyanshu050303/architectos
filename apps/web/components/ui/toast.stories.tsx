import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "./button";
import { toast, Toaster } from "./toast";

const meta: Meta<typeof Toaster> = { title: "UI/Toast", component: Toaster };
export default meta;

type Story = StoryObj<typeof Toaster>;

export const Tones: Story = {
  render: () => (
    <div className="flex h-64 flex-wrap items-start gap-2">
      <Button onClick={() => toast("Architecture v4 saved", { tone: "success" })}>Success</Button>
      <Button onClick={() => toast("Copied link to this view")}>Neutral</Button>
      <Button
        onClick={() =>
          toast("Validation failed", {
            tone: "danger",
            description: "No changes were applied. Request ID req_123.",
          })
        }
      >
        Danger
      </Button>
      <Toaster />
    </div>
  ),
};
