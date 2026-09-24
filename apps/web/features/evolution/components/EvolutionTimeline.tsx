"use client";

import { CheckCircle2, CircleDashed, CircleDot, type LucideIcon } from "lucide-react";
import { useRef } from "react";

import { formatCompact } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type { EvolutionStage } from "@/types/evolution";

export const STAGE_STATUS_META: Record<
  EvolutionStage["status"],
  { label: string; Icon: LucideIcon; iconClass: string }
> = {
  past: { label: "Past", Icon: CheckCircle2, iconClass: "text-fg-secondary" },
  current: { label: "Current", Icon: CircleDot, iconClass: "text-info-fg" },
  planned: { label: "Planned", Icon: CircleDashed, iconClass: "text-muted" },
};

export interface EvolutionTimelineProps {
  stages: readonly EvolutionStage[];
  selectedId: string;
  onSelect: (stageId: string) => void;
  /** id of the tabpanel the tabs control. */
  panelId: string;
  /** Prefix for tab ids, so the panel can reference the selected tab. */
  idPrefix: string;
}

export function stageTabId(idPrefix: string, stageId: string): string {
  return `${idPrefix}-tab-${stageId}`;
}

/**
 * Horizontal roadmap V1 ●──── V2 ●──── V3 ● (spec §42). A tablist with a roving tabindex:
 * arrow keys move between stages, Home/End jump to the ends, selection follows focus.
 */
export function EvolutionTimeline({
  stages,
  selectedId,
  onSelect,
  panelId,
  idPrefix,
}: EvolutionTimelineProps) {
  const tabRefs = useRef(new Map<string, HTMLButtonElement>());

  const move = (index: number) => {
    const stage = stages[index];
    if (!stage) return;
    onSelect(stage.id);
    tabRefs.current.get(stage.id)?.focus();
  };

  const onKeyDown = (event: React.KeyboardEvent, index: number) => {
    const last = stages.length - 1;
    let next: number | null = null;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") next = index === last ? 0 : index + 1;
    else if (event.key === "ArrowLeft" || event.key === "ArrowUp") next = index === 0 ? last : index - 1;
    else if (event.key === "Home") next = 0;
    else if (event.key === "End") next = last;
    if (next === null) return;
    event.preventDefault();
    move(next);
  };

  return (
    <div className="-mx-1 overflow-x-auto px-1 pb-1">
      <div
        role="tablist"
        aria-label="Evolution stages"
        aria-orientation="horizontal"
        className="flex min-w-max"
      >
        {stages.map((stage, index) => {
          const selected = stage.id === selectedId;
          const meta = STAGE_STATUS_META[stage.status];
          const next = stages[index + 1];
          const incomingPlanned = stage.status === "planned";
          const outgoingPlanned = next?.status === "planned";
          return (
            <button
              key={stage.id}
              ref={(el) => {
                if (el) tabRefs.current.set(stage.id, el);
                else tabRefs.current.delete(stage.id);
              }}
              type="button"
              role="tab"
              id={stageTabId(idPrefix, stage.id)}
              aria-selected={selected}
              aria-controls={panelId}
              tabIndex={selected ? 0 : -1}
              onClick={() => onSelect(stage.id)}
              onKeyDown={(e) => onKeyDown(e, index)}
              className={cn(
                "flex w-44 shrink-0 flex-col items-center gap-2 rounded-md border px-3 py-3 text-center sm:w-auto sm:min-w-44 sm:flex-1",
                "transition-colors motion-reduce:transition-none",
                "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                selected ? "border-strong bg-surface-2" : "border-transparent hover:bg-surface-2/60",
              )}
            >
              <span className="text-sm font-semibold text-fg">{stage.label}</span>
              <span className="tabular text-xs text-fg-secondary">
                {formatCompact(stage.dailyActiveUsers)} DAU
              </span>

              {/* Connector: left half links to the previous stage, right half to the next. */}
              <span aria-hidden className="relative flex h-4 w-full items-center justify-center">
                {index > 0 ? (
                  <span
                    className={cn(
                      "absolute top-1/2 right-1/2 -left-[13px] border-t",
                      incomingPlanned ? "border-dashed border-strong" : "border-strong",
                    )}
                  />
                ) : null}
                {next ? (
                  <span
                    className={cn(
                      "absolute top-1/2 -right-[13px] left-1/2 border-t",
                      outgoingPlanned ? "border-dashed border-strong" : "border-strong",
                    )}
                  />
                ) : null}
                <span
                  className={cn(
                    "relative z-10 size-3.5 rounded-full border-2",
                    stage.status === "past" && "border-fg-secondary bg-fg-secondary",
                    stage.status === "current" && "border-info bg-info ring-4 ring-info-soft",
                    stage.status === "planned" && "border-dashed border-strong bg-surface",
                  )}
                />
              </span>

              <span className="flex items-center gap-1 text-xs text-fg-secondary">
                <meta.Icon aria-hidden className={cn("size-3.5", meta.iconClass)} />
                {meta.label}
              </span>
            </button>
          );
        })}
      </div>
    </div>
  );
}
