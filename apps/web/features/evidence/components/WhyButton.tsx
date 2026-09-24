"use client";

import { CircleHelp } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Tooltip } from "@/components/ui/tooltip";
import { cn } from "@/lib/utils";
import { useUiStore } from "@/stores/ui-store";

export interface WhyButtonProps {
  evidenceId: string | null;
  /** Visible text. Defaults to "Why?". */
  label?: string;
  /** What the evidence explains, e.g. "PostgreSQL connections"; included in the accessible name. */
  subject?: string;
  className?: string;
}

/** Opens the evidence drawer (spec §34). Disabled, with an explanation, when no evidence exists. */
export function WhyButton({ evidenceId, label = "Why?", subject, className }: WhyButtonProps) {
  const openEvidence = useUiStore((s) => s.openEvidence);
  const accessibleName = subject ? `${label} ${subject}` : undefined;

  if (!evidenceId) {
    return (
      <Tooltip content="No evidence was recorded for this value">
        {/* Disabled buttons receive no pointer events, so the span carries the tooltip. */}
        <span tabIndex={0} className="inline-flex rounded-sm">
          <Button
            variant="ghost"
            size="sm"
            disabled
            aria-label={accessibleName}
            className={cn("h-6 px-1.5", className)}
          >
            <CircleHelp aria-hidden className="size-3.5" />
            {label}
          </Button>
        </span>
      </Tooltip>
    );
  }

  return (
    <Button
      variant="ghost"
      size="sm"
      aria-label={accessibleName}
      aria-haspopup="dialog"
      onClick={() => openEvidence(evidenceId)}
      className={cn("h-6 px-1.5", className)}
    >
      <CircleHelp aria-hidden className="size-3.5" />
      {label}
    </Button>
  );
}
