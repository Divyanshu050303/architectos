"use client";

/** Canvas toolbar (spec §22): view controls, history, layout, add, analysis mode and draft state. */
import { PanelRight, Redo2, Undo2 } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";

import { CanvasViewControls } from "./toolbar/CanvasViewControls";
import { DiscardButton, DraftStatus, SaveButton } from "./toolbar/DraftControls";
import { EditMenus } from "./toolbar/EditMenus";
import { ModeControl } from "./toolbar/ModeControl";
import { Divider, ToolbarGroup } from "./toolbar/primitives";
import type { ArchitectureToolbarProps } from "./toolbar/types";
import { VersionMenu } from "./toolbar/VersionMenu";
import { ViewControl } from "./toolbar/ViewControl";

export type { ArchitectureToolbarProps } from "./toolbar/types";

export function ArchitectureToolbar(props: ArchitectureToolbarProps) {
  const { editable, pendingCount } = props;
  const dirty = pendingCount > 0;

  return (
    <div
      role="toolbar"
      aria-label="Canvas controls"
      className="@container flex h-11 shrink-0 items-center gap-1 overflow-x-auto border-b border-default bg-surface px-2"
    >
      {editable ? (
        <EditMenus
          onAddComponent={props.onAddComponent}
          onAutoLayout={props.onAutoLayout}
          domainsAvailable={props.domainsAvailable}
        />
      ) : null}

      <CanvasViewControls
        onZoomIn={props.onZoomIn}
        onZoomOut={props.onZoomOut}
        onFitView={props.onFitView}
        showGrid={props.showGrid}
        onToggleGrid={props.onToggleGrid}
        showMiniMap={props.showMiniMap}
        onToggleMiniMap={props.onToggleMiniMap}
        isFullscreen={props.isFullscreen}
        onToggleFullscreen={props.onToggleFullscreen}
      />

      {editable ? (
        <>
          <Divider />
          <ToolbarGroup>
            <IconButton label="Undo" shortcut="mod+z" disabled={!props.canUndo} onClick={props.onUndo}>
              <Undo2 aria-hidden />
            </IconButton>
            <IconButton label="Redo" shortcut="mod+shift+z" disabled={!props.canRedo} onClick={props.onRedo}>
              <Redo2 aria-hidden />
            </IconButton>
          </ToolbarGroup>
        </>
      ) : null}

      <Divider />
      <ViewControl
        view={props.view}
        onViewChange={props.onViewChange}
        focusHops={props.focusHops}
        onFocusHopsChange={props.onFocusHopsChange}
        domainsAvailable={props.domainsAvailable}
      />
      <Divider />
      <ModeControl mode={props.mode} onModeChange={props.onModeChange} />

      <div className="min-w-4 flex-1" />

      <ToolbarGroup className="gap-2">
        <DraftStatus pendingCount={pendingCount} savedVersion={props.savedVersion} />
        <VersionMenu
          versions={props.versions}
          savedVersion={props.savedVersion}
          onCompare={props.onCompare}
        />
        {editable && dirty ? <DiscardButton pendingCount={pendingCount} onDiscard={props.onDiscard} /> : null}
        {editable ? <SaveButton dirty={dirty} isSaving={props.isSaving} onSave={props.onSave} /> : null}
        <IconButton
          label={props.inspectorOpen ? "Hide inspector" : "Show inspector"}
          active={props.inspectorOpen}
          aria-pressed={props.inspectorOpen}
          onClick={props.onToggleInspector}
        >
          <PanelRight aria-hidden />
        </IconButton>
      </ToolbarGroup>
    </div>
  );
}
