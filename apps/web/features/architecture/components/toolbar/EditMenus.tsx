"use client";

import { ChevronDown, Network, Plus } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";

import { COMPONENT_LIBRARY, COMPONENT_TYPE_META } from "../../constants";
import { AUTO_LAYOUT_LABELS } from "../../utils/graph-layout";
import { Divider, ToolbarGroup } from "./primitives";
import type { ArchitectureToolbarProps } from "./types";

/** Editing entry points (spec §22): add from the component library and auto layout. */
export function EditMenus({
  onAddComponent,
  onAutoLayout,
  domainsAvailable,
}: Pick<ArchitectureToolbarProps, "onAddComponent" | "onAutoLayout" | "domainsAvailable">) {
  return (
    <ToolbarGroup>
      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant="secondary" aria-label="Add component">
            <Plus aria-hidden className="size-3.5" />
            <span className="hidden @min-[74rem]:inline">Add component</span>
            <ChevronDown aria-hidden className="size-3 text-muted" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start" className="max-h-96 overflow-y-auto">
          <DropdownMenuLabel>Component library</DropdownMenuLabel>
          {COMPONENT_LIBRARY.map((definition) => {
            const { Icon, category } = COMPONENT_TYPE_META[definition.type];
            return (
              <DropdownMenuItem
                key={`${definition.type}-${definition.name}`}
                onSelect={() => onAddComponent(definition)}
              >
                <Icon aria-hidden />
                <span className="flex-1">{definition.name}</span>
                <span className="text-2xs text-muted">{category}</span>
              </DropdownMenuItem>
            );
          })}
        </DropdownMenuContent>
      </DropdownMenu>

      <DropdownMenu>
        <DropdownMenuTrigger asChild>
          <Button size="sm" variant="ghost" aria-label="Auto layout">
            <Network aria-hidden className="size-3.5" />
            <span className="hidden @min-[74rem]:inline">Auto layout</span>
            <ChevronDown aria-hidden className="size-3 text-muted" />
          </Button>
        </DropdownMenuTrigger>
        <DropdownMenuContent align="start">
          <DropdownMenuItem onSelect={() => onAutoLayout("hierarchical-tb")}>
            {AUTO_LAYOUT_LABELS["hierarchical-tb"]}
          </DropdownMenuItem>
          <DropdownMenuItem onSelect={() => onAutoLayout("hierarchical-lr")}>
            {AUTO_LAYOUT_LABELS["hierarchical-lr"]}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <DropdownMenuItem onSelect={() => onAutoLayout("force")}>
            {AUTO_LAYOUT_LABELS.force}
          </DropdownMenuItem>
          <DropdownMenuItem disabled={!domainsAvailable} onSelect={() => onAutoLayout("domain")}>
            {AUTO_LAYOUT_LABELS.domain}
            {domainsAvailable ? null : <span className="ml-auto text-2xs text-muted">No domains</span>}
          </DropdownMenuItem>
          <DropdownMenuSeparator />
          <p className="px-2 py-1 text-2xs text-muted">Manual: drag components to place them.</p>
        </DropdownMenuContent>
      </DropdownMenu>
      <Divider />
    </ToolbarGroup>
  );
}
