"use client";

import { Tabs as RadixTabs } from "radix-ui";

import { cn } from "@/lib/utils";

export const Tabs = RadixTabs.Root;

export function TabsList({ className, ...props }: React.ComponentProps<typeof RadixTabs.List>) {
  return (
    <RadixTabs.List
      className={cn("flex items-center gap-1 overflow-x-auto border-b border-default px-3", className)}
      {...props}
    />
  );
}

export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof RadixTabs.Trigger>) {
  return (
    <RadixTabs.Trigger
      className={cn(
        "-mb-px h-9 shrink-0 border-b-2 border-transparent px-2 text-xs font-medium text-muted transition-colors",
        "hover:text-fg data-[state=active]:border-accent-strong data-[state=active]:text-fg",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof RadixTabs.Content>) {
  return <RadixTabs.Content className={cn("focus-visible:outline-none", className)} {...props} />;
}
