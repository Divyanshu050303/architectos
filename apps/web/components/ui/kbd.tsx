"use client";

import { useSyncExternalStore } from "react";

import { formatShortcut, isMacPlatform } from "@/lib/keyboard";
import { cn } from "@/lib/utils";

const subscribe = () => () => {};

export function Kbd({ shortcut, className }: { shortcut: string; className?: string }) {
  const mac = useSyncExternalStore(subscribe, isMacPlatform, () => true);
  return (
    <kbd
      className={cn(
        "tabular inline-flex h-5 items-center rounded-sm border border-default bg-surface-2 px-1.5 text-2xs text-muted",
        className,
      )}
    >
      {formatShortcut(shortcut, mac)}
    </kbd>
  );
}
