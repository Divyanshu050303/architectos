"use client";

/**
 * Chrome floating over the canvas (spec §68–72): the connect / domain banner, the
 * overlay summary and hints, the simulation result and playback, the legend and the
 * AI proposal. Each region sits in its own error boundary, so a failure in one (e.g.
 * the simulation) never takes down the canvas or the rest of the workspace (spec §82).
 */
import { Layers, X } from "lucide-react";

import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import type { AnalysisMode } from "@/types/architecture";
import type { SimulationResult } from "@/types/simulation";

import { domainLabel, type GraphView } from "../utils/graph-view";
import type { OverlayChrome } from "../utils/overlay-chrome";
import type { PlaybackStep } from "../utils/simulation-playback";
import { OverlayHint, OverlayLegend, OverlaySummary } from "./OverlayChrome";
import { ProposalPanel, type ProposalPanelProps } from "./ProposalPanel";
import { SimulationPlayback, SimulationResultCard } from "./SimulationOverlay";

export interface WorkspaceSimulationChrome {
  result: SimulationResult | null;
  scenarioLabel: string | null;
  href: string;
  steps: readonly PlaybackStep[];
  stepIndex: number;
  onStepChange: (index: number) => void;
  onPlayingChange: (playing: boolean) => void;
}

export interface WorkspaceOverlaysProps {
  mode: AnalysisMode;
  view: GraphView;
  expandedDomain: string | null;
  onCollapseDomain: () => void;
  /** Name of the component a connection is being drawn from. */
  connectSourceName: string | null;
  chrome: OverlayChrome;
  focusHint: string | null;
  simulation: WorkspaceSimulationChrome;
  /** The open AI proposal; replaces the playback and legend while shown. */
  proposal: Omit<ProposalPanelProps, "className"> | null;
}

export function WorkspaceOverlays({
  mode,
  view,
  expandedDomain,
  onCollapseDomain,
  connectSourceName,
  chrome,
  focusHint,
  simulation,
  proposal,
}: WorkspaceOverlaysProps) {
  return (
    <>
      {connectSourceName ? (
        <div
          role="status"
          className="absolute top-3 left-1/2 z-10 -translate-x-1/2 rounded-md border border-accent/50 bg-surface px-3 py-1.5 text-xs text-fg shadow-raised"
        >
          Select a component to connect from <span className="font-medium">{connectSourceName}</span>
          <span className="text-muted"> · Esc to cancel</span>
        </div>
      ) : view === "overview" && expandedDomain ? (
        <div className="absolute top-3 left-1/2 z-10 flex -translate-x-1/2 items-center gap-1.5 rounded-full border border-default bg-surface py-0.5 pr-0.5 pl-3 text-xs shadow-subtle">
          <Layers aria-hidden className="size-3.5 text-muted" />
          <span className="text-muted">Overview</span>
          <span aria-hidden className="text-muted">
            /
          </span>
          <span className="font-medium text-fg">{domainLabel(expandedDomain)}</span>
          <button
            type="button"
            onClick={onCollapseDomain}
            aria-label={`Collapse ${domainLabel(expandedDomain)}`}
            className="ml-1 inline-flex size-6 items-center justify-center rounded-full text-muted hover:bg-surface-2 hover:text-fg"
          >
            <X aria-hidden className="size-3.5" />
          </button>
        </div>
      ) : null}

      <div className="pointer-events-none absolute top-3 left-3 z-10 flex max-w-[calc(100%-1.5rem)] flex-col items-start gap-2 [&>*]:pointer-events-auto">
        <ErrorBoundary label="Overlay summary">
          {chrome.summary ? <OverlaySummary {...chrome.summary} /> : null}
          {chrome.hint ? <OverlayHint {...chrome.hint} /> : null}
          {focusHint ? (
            <p className="rounded-md border border-default bg-surface px-3 py-1.5 text-xs text-fg-secondary shadow-subtle">
              {focusHint}
            </p>
          ) : null}
        </ErrorBoundary>
      </div>

      {mode === "simulation" && simulation.result ? (
        <div className="absolute top-3 right-3 z-10">
          <ErrorBoundary label="Simulation result">
            <SimulationResultCard
              scenarioLabel={simulation.scenarioLabel}
              result={simulation.result}
              href={simulation.href}
            />
          </ErrorBoundary>
        </div>
      ) : null}

      {proposal ? (
        <div className="pointer-events-none absolute inset-3 z-10 flex flex-col items-start justify-end [&>*]:pointer-events-auto">
          <ErrorBoundary key={proposal.proposal.id} label="Proposal">
            <ProposalPanel {...proposal} className="max-h-full" />
          </ErrorBoundary>
        </div>
      ) : (
        <div className="pointer-events-none absolute bottom-3 left-3 z-10 flex max-w-[calc(100%-15rem)] flex-col items-start gap-2 [&>*]:pointer-events-auto">
          {mode === "simulation" && simulation.steps.length > 1 ? (
            <ErrorBoundary label="Simulation playback">
              <SimulationPlayback
                steps={simulation.steps}
                index={simulation.stepIndex}
                onIndexChange={simulation.onStepChange}
                onPlayingChange={simulation.onPlayingChange}
              />
            </ErrorBoundary>
          ) : null}
          <ErrorBoundary label="Legend">
            <OverlayLegend mode={mode} />
          </ErrorBoundary>
        </div>
      )}
    </>
  );
}
