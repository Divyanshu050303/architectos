"use client";

import { ChevronDown, Focus, LayoutGrid, Workflow } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { IconButton } from "@/components/ui/icon-button";

import { GRAPH_VIEW_LABELS, GRAPH_VIEWS, type GraphView } from "../../utils/graph-view";
import { ToolbarGroup, WIDE_ONLY } from "./primitives";
import type { ArchitectureToolbarProps } from "./types";

const VIEW_ICONS: Record<GraphView, typeof LayoutGrid> = {
  overview: LayoutGrid,
  detailed: Workflow,
  focused: Focus,
};

const VIEW_HINTS: Record<GraphView, string> = {
  overview: "Overview · components collapsed into domains",
  detailed: "Detailed · every component",
  focused: "Focused · selected component and its neighbours",
};

function CurrentViewIcon({ view }: { view: GraphView }) {
  const Icon = VIEW_ICONS[view];
  return <Icon aria-hidden className="size-4" />;
}

/** Large graph strategy (spec §65): Overview / Detailed / Focused. */
export function ViewControl({
  view,
  onViewChange,
  focusHops,
  onFocusHopsChange,
  domainsAvailable,
}: Pick<
  ArchitectureToolbarProps,
  "view" | "onViewChange" | "focusHops" | "onFocusHopsChange" | "domainsAvailable"
>) {
  return (
    <ToolbarGroup>
      <div role="radiogroup" aria-label="Graph level" className={WIDE_ONLY}>
        {GRAPH_VIEWS.map((v) => {
          const Icon = VIEW_ICONS[v];
          const disabled = v === "overview" && !domainsAvailable;
          return (
            <IconButton
              key={v}
              role="radio"
              aria-checked={v === view}
              label={disabled ? "Overview · components have no domains" : VIEW_HINTS[v]}
              active={v === view}
              disabled={disabled}
              onClick={() => onViewChange(v)}
            >
              <Icon aria-hidden />
              <span className="sr-only">{GRAPH_VIEW_LABELS[v]}</span>
            </IconButton>
          );
        })}
      </div>
      <div className="@min-[64rem]:hidden">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="sm"
              variant="ghost"
              aria-label={`Graph level: ${GRAPH_VIEW_LABELS[view]}`}
              className="px-1.5"
            >
              <CurrentViewIcon view={view} />
              <ChevronDown aria-hidden className="size-3 text-muted" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuLabel>Graph level</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={view} onValueChange={(v) => onViewChange(v as GraphView)}>
              {GRAPH_VIEWS.map((v) => (
                <DropdownMenuRadioItem key={v} value={v} disabled={v === "overview" && !domainsAvailable}>
                  {VIEW_HINTS[v]}
                </DropdownMenuRadioItem>
              ))}
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      {view === "focused" ? (
        <Button
          size="sm"
          variant="ghost"
          aria-label={`Neighbourhood depth: ${focusHops} ${focusHops === 1 ? "hop" : "hops"}. Switch to ${focusHops === 1 ? 2 : 1}.`}
          onClick={() => onFocusHopsChange(focusHops === 1 ? 2 : 1)}
          className="tabular px-1.5"
        >
          {focusHops} {focusHops === 1 ? "hop" : "hops"}
        </Button>
      ) : null}
    </ToolbarGroup>
  );
}
