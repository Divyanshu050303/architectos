import type { Meta, StoryObj } from "@storybook/nextjs-vite";

import { Button } from "./button";
import { Dialog, DialogClose, DialogContent, DialogFooter, DialogTrigger } from "./dialog";
import { Field, Input } from "./input";

const meta: Meta<typeof DialogContent> = { title: "UI/Dialog", component: DialogContent };
export default meta;

type Story = StoryObj<typeof DialogContent>;

export const CreateProject: Story = {
  render: () => (
    <Dialog defaultOpen>
      <DialogTrigger asChild>
        <Button variant="primary">New system</Button>
      </DialogTrigger>
      <DialogContent title="Create system" description="Name the system you want to design.">
        <Field label="Name">
          {({ id, describedBy, invalid }) => (
            <Input
              id={id}
              aria-describedby={describedBy}
              aria-invalid={invalid}
              defaultValue="Food Delivery"
            />
          )}
        </Field>
        <DialogFooter>
          <DialogClose asChild>
            <Button variant="ghost">Cancel</Button>
          </DialogClose>
          <Button variant="primary">Create</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  ),
};
