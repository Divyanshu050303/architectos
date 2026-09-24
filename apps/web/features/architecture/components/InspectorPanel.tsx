"use client";

/**
 * Docked inspector panel (spec §28): slides in and out with CSS driven by
 * `data-state` (spec §100), stays mounted just long enough for the exit, and is inert
 * while closing. Its content sits in an error boundary (spec §82).
 */
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";

import { usePresence } from "../hooks/usePresence";
import { ArchitectureInspector, type ArchitectureInspectorProps } from "./ArchitectureInspector";

/** Matches `.motion-panel[data-state="closed"]` in styles/architecture.css. */
const PANEL_EXIT_MS = 140;

export interface InspectorPanelProps extends ArchitectureInspectorProps {
  open: boolean;
}

export function InspectorPanel({ open, ...inspector }: InspectorPanelProps) {
  const presence = usePresence(open, PANEL_EXIT_MS);
  if (!presence.mounted) return null;
  const closing = presence.state === "closed";
  return (
    <aside
      aria-label="Inspector"
      data-state={presence.state}
      inert={closing}
      aria-hidden={closing || undefined}
      className="motion-panel absolute inset-y-0 right-0 z-20 w-full max-w-sm border-l border-default bg-surface shadow-raised lg:static lg:w-80 lg:max-w-none lg:shadow-none"
    >
      <ErrorBoundary label="Inspector">
        <ArchitectureInspector {...inspector} />
      </ErrorBoundary>
    </aside>
  );
}
