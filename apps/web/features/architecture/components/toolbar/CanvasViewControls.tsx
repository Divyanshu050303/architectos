"use client";

import {
  Grid3x3,
  Map as MapIcon,
  Maximize,
  Maximize2,
  Minimize2,
  MoreHorizontal,
  ZoomIn,
  ZoomOut,
} from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { IconButton } from "@/components/ui/icon-button";

import { ToolbarGroup, WIDE_ONLY } from "./primitives";
import type { ArchitectureToolbarProps } from "./types";

type CanvasViewControlsProps = Pick<
  ArchitectureToolbarProps,
  | "onZoomIn"
  | "onZoomOut"
  | "onFitView"
  | "showGrid"
  | "onToggleGrid"
  | "showMiniMap"
  | "onToggleMiniMap"
  | "isFullscreen"
  | "onToggleFullscreen"
>;

/** Zoom, fit, grid, minimap and fullscreen; folded into a menu on narrow toolbars. */
export function CanvasViewControls(props: CanvasViewControlsProps) {
  return (
    <ToolbarGroup>
      {/* Narrow toolbars (e.g. inspector open) fold zoom, grid and minimap into a menu. */}
      <div className={WIDE_ONLY}>
        <IconButton label="Zoom out" onClick={props.onZoomOut}>
          <ZoomOut aria-hidden />
        </IconButton>
        <IconButton label="Zoom in" onClick={props.onZoomIn}>
          <ZoomIn aria-hidden />
        </IconButton>
      </div>
      <IconButton label="Fit architecture" shortcut="f" onClick={props.onFitView}>
        <Maximize aria-hidden />
      </IconButton>
      <div className={WIDE_ONLY}>
        <IconButton
          label={props.showGrid ? "Hide grid" : "Show grid"}
          active={props.showGrid}
          aria-pressed={props.showGrid}
          onClick={props.onToggleGrid}
        >
          <Grid3x3 aria-hidden />
        </IconButton>
        <IconButton
          label={props.showMiniMap ? "Hide minimap" : "Show minimap"}
          active={props.showMiniMap}
          aria-pressed={props.showMiniMap}
          onClick={props.onToggleMiniMap}
        >
          <MapIcon aria-hidden />
        </IconButton>
      </div>
      <div className="@min-[64rem]:hidden">
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button size="sm" variant="ghost" aria-label="More view controls" className="px-1.5">
              <MoreHorizontal aria-hidden className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="start">
            <DropdownMenuItem onSelect={props.onZoomIn}>
              <ZoomIn aria-hidden />
              Zoom in
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={props.onZoomOut}>
              <ZoomOut aria-hidden />
              Zoom out
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem onSelect={props.onToggleGrid}>
              <Grid3x3 aria-hidden />
              {props.showGrid ? "Hide grid" : "Show grid"}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={props.onToggleMiniMap}>
              <MapIcon aria-hidden />
              {props.showMiniMap ? "Hide minimap" : "Show minimap"}
            </DropdownMenuItem>
            <DropdownMenuItem onSelect={props.onToggleFullscreen}>
              {props.isFullscreen ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
              {props.isFullscreen ? "Exit fullscreen" : "Fullscreen"}
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
      <div className={WIDE_ONLY}>
        <IconButton
          label={props.isFullscreen ? "Exit fullscreen" : "Fullscreen"}
          onClick={props.onToggleFullscreen}
        >
          {props.isFullscreen ? <Minimize2 aria-hidden /> : <Maximize2 aria-hidden />}
        </IconButton>
      </div>
    </ToolbarGroup>
  );
}
