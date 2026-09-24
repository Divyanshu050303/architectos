"use client";

/**
 * Entry of the architecture workspace (spec §19): loads the architecture, feeds the
 * local draft and shows the skeleton, error or empty state until WorkspaceView can
 * render. Project-scoped UI state is reset when the project changes.
 */
import { ReactFlowProvider } from "@xyflow/react";
import { useParams } from "next/navigation";
import { useEffect } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { useArchitecture } from "@/hooks/use-architecture";
import { useArchitectureStore } from "@/stores/architecture-store";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";

import { CanvasEmptyState } from "./CanvasEmptyState";
import { WorkspaceSkeleton } from "./WorkspaceSkeleton";
import { WorkspaceView } from "./WorkspaceView";

export { WorkspaceSkeleton };

export function ArchitectureWorkspace() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const architectureQuery = useArchitecture(projectId);
  const data = architectureQuery.data;
  const load = useArchitectureStore((s) => s.load);
  const present = useArchitectureStore((s) => s.present);

  useEffect(() => {
    if (data) load(data);
  }, [data, load]);

  // Selection, highlights and proposals belong to one project.
  useEffect(
    () => () => {
      const ws = useWorkspaceStore.getState();
      ws.clearSelection();
      ws.clearHighlight();
      ws.cancelConnect();
      ws.closeInspector();
      useCommandStore.getState().clearProposal();
    },
    [projectId],
  );

  if (architectureQuery.isPending) return <WorkspaceSkeleton />;
  if (architectureQuery.isError) {
    const info = getErrorInfo(architectureQuery.error);
    return (
      <div className="p-6">
        <ErrorState
          title="The architecture could not be loaded."
          message={info.message}
          requestId={info.requestId}
          onRetry={() => void architectureQuery.refetch()}
        />
      </div>
    );
  }
  if (!data) return <CanvasEmptyState projectId={projectId} />;
  if (!present || present.projectId !== data.projectId) return <WorkspaceSkeleton />;

  return (
    <ReactFlowProvider>
      <WorkspaceView projectId={projectId} />
    </ReactFlowProvider>
  );
}
