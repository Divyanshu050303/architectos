import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Kbd } from "./kbd";

const meta: Meta<typeof Kbd> = { title: "UI/Kbd", component: Kbd, args: { shortcut: "mod+k" } };
export default meta;

type Story = StoryObj<typeof Kbd>;

export const Single: Story = {};

export const Shortcuts: Story = {
  render: () => (
    <ul className="flex flex-col gap-2 text-sm text-fg-secondary">
      {[
        ["Command palette", "mod+k"],
        ["Undo", "mod+z"],
        ["Redo", "mod+shift+z"],
        ["Save", "mod+s"],
        ["Fit view", "f"],
        ["Close", "esc"],
      ].map(([label, shortcut]) => (
        <li key={label} className="flex w-56 items-center justify-between">
          {label}
          <Kbd shortcut={shortcut ?? ""} />
        </li>
      ))}
    </ul>
  ),
};
