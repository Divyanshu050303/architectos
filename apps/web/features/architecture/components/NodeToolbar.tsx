"use client";

/** Contextual actions for a single selected component (spec §27). */
import { NodeToolbar as FlowNodeToolbar, Position } from "@xyflow/react";
import { Copy, Plus, ShieldAlert, SlidersHorizontal, Trash2, Zap } from "lucide-react";

import { IconButton } from "@/components/ui/icon-button";
import { Separator } from "@/components/ui/separator";

export interface NodeToolbarProps {
  nodeId: string;
  nodeName: string;
  editable: boolean;
  onAddConnection: () => void;
  onConfigure: () => void;
  onDuplicate: () => void;
  onExplain: () => void;
  onViewConstraints: () => void;
  /** Opens the simulation page for a failure of this component (spec §27, §40). */
  onSimulateFailure: () => void;
  onDelete: () => void;
}

export function NodeToolbar({
  nodeId,
  nodeName,
  editable,
  onAddConnection,
  onConfigure,
  onDuplicate,
  onExplain,
  onViewConstraints,
  onSimulateFailure,
  onDelete,
}: NodeToolbarProps) {
  return (
    <FlowNodeToolbar nodeId={nodeId} isVisible position={Position.Top} offset={10} className="nodrag nopan">
      <div
        role="toolbar"
        aria-label={`${nodeName} actions`}
        className="flex items-center gap-0.5 rounded-md border border-default bg-surface p-0.5 shadow-raised"
      >
        {editable ? (
          <IconButton size="sm" label="Add connection" onClick={onAddConnection}>
            <Plus aria-hidden />
          </IconButton>
        ) : null}
        <IconButton size="sm" label="Configure" onClick={onConfigure}>
          <SlidersHorizontal aria-hidden />
        </IconButton>
        {editable ? (
          <IconButton size="sm" label="Duplicate" onClick={onDuplicate}>
            <Copy aria-hidden />
          </IconButton>
        ) : null}
        <IconButton size="sm" label={`Explain ${nodeName}`} onClick={onExplain} className="text-accent-fg">
          <span aria-hidden className="text-sm leading-none">
            ✦
          </span>
        </IconButton>
        <IconButton size="sm" label={`Simulate ${nodeName} failure`} onClick={onSimulateFailure}>
          <Zap aria-hidden />
        </IconButton>
        <IconButton size="sm" label="View constraints" onClick={onViewConstraints}>
          <ShieldAlert aria-hidden />
        </IconButton>
        {editable ? (
          <>
            <Separator orientation="vertical" className="mx-0.5 h-4" />
            <IconButton
              size="sm"
              label="Delete"
              shortcut="delete"
              onClick={onDelete}
              className="hover:bg-danger-soft hover:text-danger-fg"
            >
              <Trash2 aria-hidden />
            </IconButton>
          </>
        ) : null}
      </div>
    </FlowNodeToolbar>
  );
}
