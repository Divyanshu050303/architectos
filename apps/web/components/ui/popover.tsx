"use client";

import { Popover as RadixPopover } from "radix-ui";

import { cn } from "@/lib/utils";

export const Popover = RadixPopover.Root;
export const PopoverTrigger = RadixPopover.Trigger;

export function PopoverContent({ className, ...props }: React.ComponentProps<typeof RadixPopover.Content>) {
  return (
    <RadixPopover.Portal>
      <RadixPopover.Content
        sideOffset={6}
        className={cn(
          "z-50 rounded-md border border-default bg-surface p-3 text-sm shadow-raised focus-visible:outline-none",
          className,
        )}
        {...props}
      />
    </RadixPopover.Portal>
  );
}
