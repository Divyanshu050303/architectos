"use client";

/**
 * Simulation overlay chrome (spec §41, §72): a playback bar that replays the backend
 * timeline on the canvas (Failure → Dependency → Load increase → Resource pressure →
 * Latency → Potential failure) and a summary card of the run's result. Replay only:
 * every state and figure comes from the simulation engine.
 */
import { Pause, Play, RotateCcw, SkipForward } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { IconButton } from "@/components/ui/icon-button";
import { Slider } from "@/components/ui/slider";
import { formatNumber, formatPercent } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type { SimulationImpact, SimulationResult } from "@/types/simulation";

import { useMediaQuery } from "../hooks/useMediaQuery";
import { PHASE_LABELS, PHASE_ORDER, type PlaybackStep, stepLabel } from "../utils/simulation-playback";

/** Time each step stays on screen while playing. */
export const PLAYBACK_STEP_MS = 1400;

export interface SimulationPlaybackProps {
  steps: readonly PlaybackStep[];
  index: number;
  onIndexChange: (index: number) => void;
  /** Told when playback starts or stops, so the canvas can mark nodes `simulating` (spec §25). */
  onPlayingChange?: (playing: boolean) => void;
  className?: string;
}

export function SimulationPlayback({
  steps,
  index,
  onIndexChange,
  onPlayingChange,
  className,
}: SimulationPlaybackProps) {
  const [playRequested, setPlaying] = useState(false);
  const reducedMotion = useMediaQuery("(prefers-reduced-motion: reduce)", false);
  const last = Math.max(steps.length - 1, 0);
  const atEnd = index >= last;
  const step = steps[Math.min(index, last)];
  // Playback stops by itself at the last step.
  const playing = playRequested && !atEnd;

  // Advance one backend timeline event per tick. Under reduced motion the canvas jumps
  // between states without transitions (the nodes' transitions are motion-safe only).
  useEffect(() => {
    if (!playing) return;
    const timer = setTimeout(() => onIndexChange(index + 1), PLAYBACK_STEP_MS);
    return () => clearTimeout(timer);
  }, [playing, index, onIndexChange]);

  useEffect(() => {
    onPlayingChange?.(playing);
  }, [playing, onPlayingChange]);
  useEffect(() => () => onPlayingChange?.(false), [onPlayingChange]);

  function togglePlay() {
    if (playing) {
      setPlaying(false);
      return;
    }
    if (atEnd) onIndexChange(0);
    setPlaying(true);
  }

  return (
    <section
      aria-label="Simulation playback"
      data-motion={reducedMotion ? "reduced" : "full"}
      data-playing={playing || undefined}
      className={cn(
        "flex w-[34rem] max-w-full flex-col gap-1.5 rounded-md border border-default bg-surface px-2.5 py-2 shadow-raised",
        className,
      )}
    >
      <div className="flex items-center gap-1">
        <IconButton
          size="sm"
          label="Reset"
          onClick={() => {
            setPlaying(false);
            onIndexChange(0);
          }}
          disabled={index === 0}
        >
          <RotateCcw aria-hidden />
        </IconButton>
        <IconButton size="sm" label={playing ? "Pause" : "Play"} aria-pressed={playing} onClick={togglePlay}>
          {playing ? <Pause aria-hidden /> : <Play aria-hidden />}
        </IconButton>
        <IconButton
          size="sm"
          label="Step"
          disabled={atEnd}
          onClick={() => {
            setPlaying(false);
            onIndexChange(Math.min(index + 1, last));
          }}
        >
          <SkipForward aria-hidden />
        </IconButton>
        <Slider
          min={0}
          max={last}
          step={1}
          value={Math.min(index, last)}
          aria-label="Simulation timeline"
          aria-valuetext={`Step ${index} of ${last}: ${stepLabel(step)}`}
          onValueChange={(next) => {
            setPlaying(false);
            onIndexChange(next);
          }}
          tone="danger"
          className="mx-2 min-w-0 flex-1"
        />
        <span className="tabular shrink-0 text-2xs text-muted">
          {index}/{last}
        </span>
      </div>

      <div className="flex min-w-0 items-baseline gap-2 px-1">
        <span role="status" aria-live="polite" className="shrink-0 text-xs font-semibold text-fg">
          {stepLabel(step)}
        </span>
        <span className="truncate text-xs text-fg-secondary">
          {step?.event ? step.event.description : "Before the scenario starts."}
        </span>
      </div>

      <ol aria-label="Propagation phases" className="flex flex-wrap items-center gap-x-1 px-1 text-2xs">
        {PHASE_ORDER.map((phase, i) => {
          const current = step?.event?.phase === phase;
          const reached = step?.reached.has(phase) ?? false;
          return (
            <li key={phase} className="flex items-center gap-1">
              {i > 0 ? (
                <span aria-hidden className="text-muted">
                  →
                </span>
              ) : null}
              <span
                aria-current={current ? "step" : undefined}
                className={cn(
                  current ? "font-semibold text-danger-fg" : reached ? "text-fg" : "text-muted",
                  !reducedMotion && "transition-colors duration-300",
                )}
              >
                {PHASE_LABELS[phase]}
              </span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}

// --- Result card --------------------------------------------------------------

const IMPACT_TONE: Record<SimulationImpact, BadgeTone> = {
  low: "neutral",
  medium: "warning",
  high: "danger",
  critical: "danger",
};

const CASCADE_LABEL: Record<SimulationResult["cascadingFailure"], string> = {
  none: "None",
  potential: "Potential",
  likely: "Likely",
};

function capitalize(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

export interface SimulationResultCardProps {
  scenarioLabel: string | null;
  result: SimulationResult;
  href: string;
  className?: string;
}

export function SimulationResultCard({ scenarioLabel, result, href, className }: SimulationResultCardProps) {
  const p99 = result.metrics.find((m) => /p99/i.test(m.metric));
  return (
    <section
      aria-label="Simulation result"
      className={cn(
        "flex w-64 flex-col gap-2 rounded-md border border-default bg-surface p-3 shadow-raised",
        className,
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="label-caps">Simulation result</span>
        <ProvenanceTag kind="calculated" label="Simulated" />
      </div>
      {scenarioLabel ? <p className="text-sm font-semibold text-fg">{scenarioLabel}</p> : null}
      <dl className="flex flex-col gap-1 text-xs">
        <div className="flex items-center justify-between gap-2">
          <dt className="text-fg-secondary">Impact</dt>
          <dd>
            <Badge tone={IMPACT_TONE[result.impact]}>{capitalize(result.impact)}</Badge>
          </dd>
        </div>
        {p99 ? (
          <div className="flex items-center justify-between gap-2">
            <dt className="text-fg-secondary">P99 latency</dt>
            <dd className="tabular text-fg">
              {formatNumber(p99.before)} → {formatNumber(p99.after)} {p99.unit}
            </dd>
          </div>
        ) : null}
        <div className="flex items-center justify-between gap-2">
          <dt className="text-fg-secondary">Error rate</dt>
          <dd className="tabular text-fg">
            {formatPercent(result.errorRate.before, 1)} → {formatPercent(result.errorRate.after, 1)}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-fg-secondary">Cascading failure</dt>
          <dd
            className={cn("font-medium", result.cascadingFailure === "none" ? "text-fg" : "text-danger-fg")}
          >
            {CASCADE_LABEL[result.cascadingFailure]}
          </dd>
        </div>
        <div className="flex items-center justify-between gap-2">
          <dt className="text-fg-secondary">Affected components</dt>
          <dd className="tabular text-fg">{result.affectedNodeIds.length}</dd>
        </div>
      </dl>
      <Link
        href={href}
        className="text-xs font-medium text-fg-secondary underline-offset-2 hover:text-fg hover:underline"
      >
        Open simulation
      </Link>
    </section>
  );
}
