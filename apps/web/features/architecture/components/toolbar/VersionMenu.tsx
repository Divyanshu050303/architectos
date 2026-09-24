"use client";

import { ChevronDown, GitCompareArrows, History } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { formatRelativeTime } from "@/lib/formatting";
import type { ArchitectureVersionSummary } from "@/types/architecture";

import type { ArchitectureToolbarProps } from "./types";

/** Version history and comparison (spec §43, §92). */
export function VersionMenu({
  versions,
  savedVersion,
  onCompare,
}: {
  versions: readonly ArchitectureVersionSummary[];
  savedVersion: number | null;
  onCompare: ArchitectureToolbarProps["onCompare"];
}) {
  const newestFirst = [...versions].sort((a, b) => b.version - a.version);
  const previous = savedVersion !== null && savedVersion > 1 ? savedVersion - 1 : null;
  return (
    <DropdownMenu>
      <DropdownMenuTrigger asChild>
        <Button size="sm" variant="ghost" aria-label="Architecture versions" className="px-1.5">
          <History aria-hidden className="size-3.5" />
          <ChevronDown aria-hidden className="hidden size-3 text-muted @min-[64rem]:inline" />
        </Button>
      </DropdownMenuTrigger>
      <DropdownMenuContent align="end" className="w-72">
        <DropdownMenuLabel>Versions</DropdownMenuLabel>
        {newestFirst.length === 0 ? (
          <p className="px-2 pb-2 text-xs text-muted">No saved versions yet.</p>
        ) : (
          newestFirst.map((v) => {
            const current = v.version === savedVersion;
            return (
              <DropdownMenuItem
                key={v.version}
                disabled={current}
                onSelect={() => onCompare(v.version, savedVersion)}
                className="items-start"
              >
                <span className="tabular w-7 shrink-0 font-medium text-fg">v{v.version}</span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="truncate text-fg">{v.summary}</span>
                  <span className="text-2xs text-muted">
                    {current ? "Current · " : "Compare with current · "}
                    {formatRelativeTime(v.createdAt)}
                  </span>
                </span>
              </DropdownMenuItem>
            );
          })
        )}
        <DropdownMenuSeparator />
        <DropdownMenuItem disabled={savedVersion === null} onSelect={() => onCompare(previous, savedVersion)}>
          <GitCompareArrows aria-hidden />
          Compare with…
        </DropdownMenuItem>
      </DropdownMenuContent>
    </DropdownMenu>
  );
}
