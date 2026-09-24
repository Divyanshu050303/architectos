"use client";

import { ChevronDown } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { cn } from "@/lib/utils";
import { type AnalysisMode, ANALYSIS_MODES, V1_ANALYSIS_MODES } from "@/types/architecture";

import { MODE_LABELS } from "../../constants";
import type { ArchitectureToolbarProps } from "./types";

const AVAILABLE_MODES = ANALYSIS_MODES.filter((m) => V1_ANALYSIS_MODES.has(m));

/**
 * Analysis overlays (spec §68). Inline on wide toolbars; a compact menu when the
 * toolbar is narrow (e.g. inspector open at 1440px), so it never overflows.
 */
export function ModeControl({ mode, onModeChange }: Pick<ArchitectureToolbarProps, "mode" | "onModeChange">) {
  return (
    <div className="flex shrink-0 items-center">
      <div
        role="radiogroup"
        aria-label="Analysis mode"
        className="hidden items-center rounded-sm border border-default bg-surface-2 p-0.5 @min-[96rem]:flex"
      >
        {AVAILABLE_MODES.map((m) => (
          <button
            key={m}
            type="button"
            role="radio"
            aria-checked={m === mode}
            onClick={() => onModeChange(m)}
            className={cn(
              "h-6 rounded-sm px-2 text-xs font-medium transition-colors",
              m === mode ? "bg-surface text-fg shadow-subtle" : "text-muted hover:text-fg",
            )}
          >
            {MODE_LABELS[m]}
          </button>
        ))}
      </div>
      <div className="@min-[96rem]:hidden">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button variant="secondary" size="sm" aria-label={`Analysis mode: ${MODE_LABELS[mode]}`}>
              <span className="hidden text-muted @min-[80rem]:inline">Overlay</span>
              <span className="text-fg">{MODE_LABELS[mode]}</span>
              <ChevronDown aria-hidden className="size-3 text-muted" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>Analysis overlay</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={mode} onValueChange={(v) => onModeChange(v as AnalysisMode)}>
              {AVAILABLE_MODES.map((m) => (
                <DropdownMenuRadioItem key={m} value={m}>
                  {MODE_LABELS[m]}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </div>
  );
}
