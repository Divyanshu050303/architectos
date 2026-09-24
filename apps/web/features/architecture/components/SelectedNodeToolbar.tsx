"use client";

/**
 * Context toolbar for the one selected component (spec §27), wired to the workspace:
 * commands, the inspector, the command bar and the simulation page.
 */
import { useRouter } from "next/navigation";

import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { ArchitectureNode } from "@/types/architecture";
import type { SimulationScenario } from "@/types/simulation";

import type { ArchitectureCommandsApi } from "../hooks/useArchitectureCommands";
import { focusCommandBar } from "./CommandBar";
import { NodeToolbar } from "./NodeToolbar";

export interface SelectedNodeToolbarProps {
  projectId: string;
  node: ArchitectureNode;
  editable: boolean;
  commands: Pick<ArchitectureCommandsApi, "duplicate" | "deleteSelection">;
  scenarios: readonly SimulationScenario[] | undefined;
}

/** Prefer a scenario that targets exactly this component, then any that includes it. */
export function scenarioFor(
  scenarios: readonly SimulationScenario[] | undefined,
  nodeId: string,
): SimulationScenario | undefined {
  return (
    scenarios?.find((s) => s.targetNodeIds.length === 1 && s.targetNodeIds[0] === nodeId) ??
    scenarios?.find((s) => s.targetNodeIds.includes(nodeId))
  );
}

export function SelectedNodeToolbar({
  projectId,
  node,
  editable,
  commands,
  scenarios,
}: SelectedNodeToolbarProps) {
  const router = useRouter();

  const simulateFailure = () => {
    const params = new URLSearchParams();
    const matching = scenarioFor(scenarios, node.id);
    if (matching) params.set("scenario", matching.id);
    params.set("node", node.id);
    router.push(`/project/${projectId}/simulation?${params.toString()}`);
  };

  return (
    <NodeToolbar
      nodeId={node.id}
      nodeName={node.name}
      editable={editable}
      onAddConnection={() => useWorkspaceStore.getState().startConnect(node.id)}
      onConfigure={() => useWorkspaceStore.getState().openInspector("configuration")}
      onDuplicate={() => commands.duplicate(node.id)}
      onExplain={() => {
        useCommandStore.getState().setPrompt(`Explain ${node.name}`);
        focusCommandBar();
      }}
      onViewConstraints={() => useWorkspaceStore.getState().openInspector("constraints")}
      onSimulateFailure={simulateFailure}
      onDelete={commands.deleteSelection}
    />
  );
}
