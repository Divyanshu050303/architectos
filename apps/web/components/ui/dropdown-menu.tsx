"use client";

import { Check } from "lucide-react";
import { DropdownMenu as RadixMenu } from "radix-ui";

import { cn } from "@/lib/utils";

import { Kbd } from "./kbd";

export const DropdownMenu = RadixMenu.Root;
export const DropdownMenuTrigger = RadixMenu.Trigger;
export const DropdownMenuRadioGroup = RadixMenu.RadioGroup;

const ITEM =
  "flex h-8 cursor-default items-center gap-2 rounded-sm px-2 text-sm text-fg outline-none select-none " +
  "data-[disabled]:opacity-40 data-[highlighted]:bg-surface-2 [&_svg]:size-4 [&_svg]:text-muted";

export function DropdownMenuContent({ className, ...props }: React.ComponentProps<typeof RadixMenu.Content>) {
  return (
    <RadixMenu.Portal>
      <RadixMenu.Content
        sideOffset={6}
        className={cn(
          "z-50 min-w-44 rounded-md border border-default bg-surface p-1 shadow-raised",
          className,
        )}
        {...props}
      />
    </RadixMenu.Portal>
  );
}

export function DropdownMenuItem({
  className,
  shortcut,
  children,
  ...props
}: React.ComponentProps<typeof RadixMenu.Item> & { shortcut?: string }) {
  return (
    <RadixMenu.Item className={cn(ITEM, className)} {...props}>
      {children}
      {shortcut ? <Kbd shortcut={shortcut} className="ml-auto" /> : null}
    </RadixMenu.Item>
  );
}

export function DropdownMenuRadioItem({
  className,
  children,
  ...props
}: React.ComponentProps<typeof RadixMenu.RadioItem>) {
  return (
    <RadixMenu.RadioItem className={cn(ITEM, "pl-7 relative", className)} {...props}>
      <RadixMenu.ItemIndicator className="absolute left-2 inline-flex">
        <Check aria-hidden />
      </RadixMenu.ItemIndicator>
      {children}
    </RadixMenu.RadioItem>
  );
}

export function DropdownMenuLabel({ className, ...props }: React.ComponentProps<typeof RadixMenu.Label>) {
  return <RadixMenu.Label className={cn("label-caps px-2 py-1.5", className)} {...props} />;
}

export function DropdownMenuSeparator() {
  return <RadixMenu.Separator className="my-1 h-px bg-default" />;
}
