"use client";

/**
 * The loaded workspace (spec §19, §57–58): wires URL state, server analyses, the
 * draft, commands and shortcuts to presentational children (toolbar, canvas, overlay
 * chrome, inspector, command bar). Overlay data comes from useWorkspaceAnalyses and
 * useSimulationReplay; overlay chrome renders in WorkspaceOverlays. Every region has
 * its own error boundary, so one failure never crashes the workspace (spec §82).
 */
import { useCallback, useEffect, useMemo } from "react";
import { useShallow } from "zustand/react/shallow";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { useArchitectureVersion } from "@/hooks/use-architecture";
import { useProposal } from "@/hooks/use-proposals";
import { selectIsDirty, useArchitectureStore } from "@/stores/architecture-store";
import { useCommandStore } from "@/stores/command-store";
import { useWorkspaceStore } from "@/stores/workspace-store";
import type { Architecture } from "@/types/architecture";

import { useArchitectureCanvas, useCanvasControls } from "../hooks/useArchitectureCanvas";
import { useArchitectureCommands, useLayoutAutosave } from "../hooks/useArchitectureCommands";
import { useArchitectureHistory } from "../hooks/useArchitectureHistory";
import { useCanvasInteractions } from "../hooks/useCanvasInteractions";
import { useConflictReload } from "../hooks/useConflictReload";
import { useCanvasEditable } from "../hooks/useMediaQuery";
import { useNodeSelection } from "../hooks/useNodeSelection";
import { useVersionTransition } from "../hooks/useVersionTransition";
import { useWorkspaceActions } from "../hooks/useWorkspaceActions";
import { useSimulationReplay, useWorkspaceAnalyses } from "../hooks/useWorkspaceAnalyses";
import { useWorkspaceFullscreen } from "../hooks/useWorkspaceFullscreen";
import { useWorkspaceUrlState } from "../hooks/useWorkspaceUrlState";
import { hasDomains } from "../utils/graph-view";
import { isOverlayLoading, overlayChrome } from "../utils/overlay-chrome";
import { ArchitectureCanvas } from "./ArchitectureCanvas";
import { CommandBar } from "./CommandBar";
import { CompareVersionsDialog } from "./CompareVersionsDialog";
import { InspectorPanel } from "./InspectorPanel";
import { SelectedNodeToolbar } from "./SelectedNodeToolbar";
import { VersionBanner } from "./VersionBanner";
import { VersionConflictDialog } from "./VersionConflictDialog";
import { WorkspaceOverlays } from "./WorkspaceOverlays";
import { WorkspaceSkeleton } from "./WorkspaceSkeleton";
import { WorkspaceToolbar } from "./WorkspaceToolbar";

export function WorkspaceView({ projectId }: { projectId: string }) {
  const canvasEditable = useCanvasEditable();

  const url = useWorkspaceUrlState({ ready: true });
  const { mode, setMode, view, setView, expandedDomain, setDomain, clearHighlight } = url;

  // --- Read-only historical version (?version=N) ------------------------------
  const base = useArchitectureStore((s) => s.base);
  const latestVersion = base?.version ?? null;
  const viewingVersion = url.version !== null && base && url.version !== base.version ? url.version : null;
  const historical = useArchitectureVersion(projectId, viewingVersion);
  const readOnly = viewingVersion !== null;
  const historicalArchitecture = readOnly ? (historical.data ?? null) : null;
  const editable = canvasEditable && !readOnly;
  /** Analyses must match this version in the history view; the draft view keeps its analyses. */
  const analysisVersion = readOnly ? viewingVersion : null;
  const shownVersion = viewingVersion ?? latestVersion;

  // --- Server analyses and simulation replay ------------------------------------------
  const analyses = useWorkspaceAnalyses({ projectId, analysisVersion, shownVersion });
  const { capacity, validation, findings, reliability, security, observability, cost } = analyses;
  const replay = useSimulationReplay({
    projectId,
    mode,
    runId: url.simulationRunId,
    analysisVersion,
    shownVersion,
  });
  const overlayLoading = isOverlayLoading(mode, { ...analyses.fetching, simulation: replay.fetching });

  const activeProposalId = useCommandStore((s) => s.activeProposalId);
  const proposal = useProposal(activeProposalId).data;
  useEffect(() => {
    // Proposals only live in the query cache (no GET endpoint); drop stale ids.
    if (activeProposalId && proposal === undefined) useCommandStore.getState().clearProposal();
  }, [activeProposalId, proposal]);

  const selection = useNodeSelection(projectId);
  const focusHops = useWorkspaceStore((s) => s.focusHops);
  const focusNodeId =
    view === "focused" && selection.selectedNodeIds.length === 1
      ? (selection.selectedNodeIds[0] ?? null)
      : null;

  const draft = useArchitectureStore((s) => s.present);
  const present = historicalArchitecture ?? draft;
  const versionChangedNodeIds = useVersionTransition(present);

  const { nodes, edges } = useArchitectureCanvas({
    mode,
    capacity,
    findings,
    proposal: readOnly ? null : proposal,
    editable,
    reliability,
    security,
    cost,
    observability,
    simulation: replay.states,
    simulationActive: replay.activeNodeIds,
    loading: overlayLoading,
    versionChangedNodeIds,
    view,
    expandedDomain,
    focusNodeId,
    focusHops,
    architecture: historicalArchitecture,
  });
  const controls = useCanvasControls();
  const commands = useArchitectureCommands(projectId);
  const history = useArchitectureHistory();
  useLayoutAutosave(projectId);

  const isDirty = useArchitectureStore(selectIsDirty);
  const domainsAvailable = useMemo(() => (present ? hasDomains(present.nodes) : false), [present]);
  const ws = useWorkspaceStore(
    useShallow((s) => ({
      showGrid: s.showGrid,
      showMiniMap: s.showMiniMap,
      isFullscreen: s.isFullscreen,
      inspectorOpen: s.inspectorOpen,
      connectSourceId: s.connectSourceId,
      highlightedNodeIds: s.highlightedNodeIds,
      highlightToken: s.highlightToken,
    })),
  );

  const canvas = useCanvasInteractions({
    present,
    view,
    expandedDomain,
    setDomain,
    clearHighlight,
    connect: commands.connect,
    selectedNodeIds: selection.selectedNodeIds,
    focusNodeId,
    focusHops,
    viewingVersion,
    highlightedNodeIds: ws.highlightedNodeIds,
    highlightToken: ws.highlightToken,
  });

  // --- Toolbar, shortcuts and palette ---------------------------------------------
  const { addComponent, autoLayout, openCompare } = useWorkspaceActions({
    projectId,
    editable,
    isDirty,
    view,
    setView,
    latestVersion,
    viewingVersion,
    controls,
    commands,
    history,
  });
  const toggleFullscreen = useWorkspaceFullscreen();

  // --- Proposal and conflict ------------------------------------------------
  const closeProposal = useCallback(() => useCommandStore.getState().clearProposal(), []);
  const onProposalApplied = useCallback((architecture: Architecture) => {
    useArchitectureStore.getState().markSaved(architecture);
    useCommandStore.getState().clearProposal();
  }, []);

  const conflict = useConflictReload(projectId);

  // --- Node context toolbar and overlay chrome ------------------------------------------
  const toolbarNode =
    selection.selectedNodeIds.length === 1 && selection.selectedEdgeIds.length === 0 && !ws.connectSourceId
      ? present?.nodes.find((n) => n.id === selection.selectedNodeIds[0])
      : undefined;
  const connectSource = ws.connectSourceId
    ? present?.nodes.find((n) => n.id === ws.connectSourceId)
    : undefined;

  const page = (segment: string) => `/project/${projectId}/${segment}`;
  const chrome = overlayChrome({
    mode,
    page,
    capacity,
    validation,
    reliability,
    security,
    cost,
    observability,
    run: replay.run,
    hasSimulationResult: replay.result !== null,
    pending: { ...analyses.pending, simulation: replay.pending },
  });
  const focusHint =
    view === "focused" && !focusNodeId ? "Select a component to focus on it and its neighbours." : null;

  return (
    <div
      className={
        ws.isFullscreen
          ? "fixed inset-0 z-40 flex flex-col overflow-hidden bg-background"
          : "flex h-full min-h-[32rem] flex-col overflow-hidden bg-background"
      }
    >
      <div className="relative flex min-h-0 flex-1">
        <div className="flex min-w-0 flex-1 flex-col">
          <ErrorBoundary label="Toolbar">
            <WorkspaceToolbar
              projectId={projectId}
              editable={editable}
              mode={mode}
              onModeChange={setMode}
              view={view}
              onViewChange={setView}
              domainsAvailable={domainsAvailable}
              controls={controls}
              history={history}
              commands={commands}
              onAddComponent={addComponent}
              onAutoLayout={autoLayout}
              onCompare={openCompare}
              onToggleFullscreen={toggleFullscreen}
            />
          </ErrorBoundary>
          {viewingVersion !== null ? (
            <VersionBanner
              version={viewingVersion}
              latestVersion={latestVersion}
              loadFailed={historical.isError}
              onCompareWithLatest={() => openCompare(viewingVersion, latestVersion)}
              onBackToLatest={() => url.setVersion(null)}
            />
          ) : null}
          <div className="relative min-h-0 flex-1">
            {readOnly && historical.isPending ? (
              <WorkspaceSkeleton />
            ) : (
              <ErrorBoundary label="Canvas">
                <ArchitectureCanvas
                  nodes={nodes}
                  edges={edges}
                  editable={editable}
                  connecting={Boolean(ws.connectSourceId)}
                  showGrid={ws.showGrid}
                  showMiniMap={ws.showMiniMap}
                  focusRequest={canvas.focusRequest}
                  fitToken={canvas.fitToken}
                  fitNodeIds={canvas.fitNodeIds}
                  onDomainActivate={canvas.activateDomain}
                  onNodeSelectionChange={selection.setNodeSelection}
                  onEdgeSelectionChange={selection.setEdgeSelection}
                  onNodeClick={canvas.onNodeClick}
                  onPaneClick={canvas.onPaneClick}
                  onConnect={canvas.onConnect}
                  onMoveNodes={commands.moveNodes}
                >
                  {toolbarNode ? (
                    <SelectedNodeToolbar
                      projectId={projectId}
                      node={toolbarNode}
                      editable={editable}
                      commands={commands}
                      scenarios={replay.scenarios}
                    />
                  ) : null}
                </ArchitectureCanvas>
              </ErrorBoundary>
            )}

            <WorkspaceOverlays
              mode={mode}
              view={view}
              expandedDomain={expandedDomain}
              onCollapseDomain={() => setDomain(null)}
              connectSourceName={connectSource?.name ?? null}
              chrome={chrome}
              focusHint={focusHint}
              simulation={{
                result: replay.result,
                scenarioLabel: replay.scenarioLabel,
                href: page("simulation"),
                steps: replay.steps,
                stepIndex: replay.stepIndex,
                onStepChange: replay.setStepIndex,
                onPlayingChange: replay.setPlaying,
              }}
              proposal={
                proposal && !readOnly
                  ? { projectId, proposal, isDirty, onApplied: onProposalApplied, onClose: closeProposal }
                  : null
              }
            />
          </div>
        </div>

        <InspectorPanel
          open={ws.inspectorOpen}
          projectId={projectId}
          mode={mode}
          capacity={capacity}
          findings={findings}
          security={security}
          observability={observability}
          cost={cost}
          architecture={historicalArchitecture}
          editable={editable}
          onCommand={commands.run}
          onDeleteSelection={commands.deleteSelection}
          onSelectNode={selection.selectNode}
        />
      </div>

      {readOnly ? null : (
        <ErrorBoundary label="Command bar">
          <CommandBar projectId={projectId} baseVersion={latestVersion} isDirty={isDirty} />
        </ErrorBoundary>
      )}
      <VersionConflictDialog
        onReload={() => void conflict.reloadLatest()}
        reloading={conflict.reloading}
        onCompare={conflict.compare}
      />
      <CompareVersionsDialog projectId={projectId} />
    </div>
  );
}
