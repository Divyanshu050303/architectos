"use client";

import { MiniMap } from "@xyflow/react";

import type { WorkspaceFlowNode } from "../hooks/useArchitectureCanvas";

function statusColor(status: string, fallback: string): string {
  switch (status) {
    case "critical":
      return "var(--danger)";
    case "warning":
      return "var(--warning)";
    default:
      return fallback;
  }
}

function nodeColor(node: WorkspaceFlowNode): string {
  switch (node.type) {
    case "boundary":
      return "transparent";
    case "domain":
      return statusColor(node.data.status, "var(--border-strong)");
    case "architecture":
      if (node.data.preview === "added") return "var(--accent)";
      return statusColor(node.data.status, node.selected ? "var(--accent-strong)" : "var(--border-strong)");
  }
}

export function ArchitectureMiniMap() {
  return (
    <MiniMap<WorkspaceFlowNode>
      position="bottom-right"
      pannable
      zoomable
      ariaLabel="Architecture overview map"
      nodeColor={nodeColor}
      nodeBorderRadius={4}
    />
  );
}
