import { formatCompact } from "@/lib/formatting";
import type { EnvelopePoint } from "@/types/capacity";
import type { EvolutionStage } from "@/types/evolution";

/**
 * Pure helpers for the operating envelope chart (spec §36). Nothing here computes capacity:
 * statuses and maxima come from the backend; these only derive labels, order and layout.
 */

export type PointStatus = EnvelopePoint["status"];

export const STATUS_TEXT: Record<PointStatus, string> = {
  current: "Current load",
  warning: "Warning",
  exceeded: "Exceeded",
  supported: "Supported",
};

/** Draw order: plain supported points first so semantic markers sit on top. */
export const DRAW_ORDER: Record<PointStatus, number> = { supported: 0, warning: 1, exceeded: 2, current: 3 };

export const STAGE_TEXT: Record<EvolutionStage["status"], string> = {
  past: "past",
  current: "current",
  planned: "planned",
};

/** Plain-language summary for the chart's accessible name. */
export function describeEnvelope(points: readonly EnvelopePoint[], maxSupported: number): string {
  const listed = points.map((p) => `${p.label} DAU: ${STATUS_TEXT[p.status]}`).join("; ");
  return (
    `Operating envelope, daily active users on a log scale. ` +
    `Supported up to ${formatCompact(maxSupported)} DAU. ${listed}.`
  );
}

// --- Version dimension (spec §36: V1 / V2 / V3 columns) ---------------------------------------

export interface EnvelopeColumn {
  stage: EvolutionStage;
  /** The stage the capacity analysis was calculated for. */
  current: boolean;
  points: Array<EnvelopePoint & { status: PointStatus }>;
}

/**
 * One column per evolution stage. The current stage shows the capacity engine's own statuses;
 * other stages only compare each load level with that stage's backend-reported maximum
 * (supported / exceeded) — no warning band is invented for them. Null when there is no version
 * dimension to show (fewer than two stages, or no current stage).
 */
export function versionColumns(
  stages: readonly EvolutionStage[],
  points: readonly EnvelopePoint[],
): EnvelopeColumn[] | null {
  if (stages.length < 2 || !stages.some((s) => s.status === "current")) return null;
  const plotted = points.filter((p) => p.dailyActiveUsers > 0);
  return stages.map((stage) => {
    const current = stage.status === "current";
    return {
      stage,
      current,
      points: plotted.map((point) => ({
        ...point,
        status: current
          ? point.status
          : point.dailyActiveUsers <= stage.maxSupportedDailyActiveUsers
            ? "supported"
            : "exceeded",
      })),
    };
  });
}

export function describeVersionEnvelope(columns: readonly EnvelopeColumn[]): string {
  const perStage = columns
    .map(({ stage, points }) => {
      const listed = points.map((p) => `${p.label} ${STATUS_TEXT[p.status].toLowerCase()}`).join(", ");
      return (
        `${stage.label} (${STAGE_TEXT[stage.status]}) supports up to ` +
        `${formatCompact(stage.maxSupportedDailyActiveUsers)} DAU: ${listed}`
      );
    })
    .join(". ");
  return `Operating envelope by architecture version, daily active users on a log scale. ${perStage}.`;
}

/** Label y per highlighted (non-supported) point, pushed apart so labels never overlap. */
export function spacedLabels(
  points: readonly EnvelopePoint[],
  y: (value: number) => number,
): Map<string, number> {
  const GAP = 13;
  const labelled = points
    .filter((p) => p.status !== "supported")
    .map((p) => ({ key: `${p.label}-${p.dailyActiveUsers}`, y: y(p.dailyActiveUsers) }))
    .sort((a, b) => a.y - b.y);
  const placed = new Map<string, number>();
  let previous = -Infinity;
  for (const label of labelled) {
    const at = Math.max(label.y, previous + GAP);
    placed.set(label.key, at);
    previous = at;
  }
  return placed;
}

/** Sideways marker offsets so two load levels closer than a marker's height do not overlap. */
export function markerOffsets(
  points: readonly EnvelopePoint[],
  y: (value: number) => number,
): Map<string, number> {
  const sorted = [...points].sort((a, b) => a.dailyActiveUsers - b.dailyActiveUsers);
  const offsets = new Map<string, number>();
  let previous: { y: number; offset: number } | null = null;
  for (const point of sorted) {
    const py = y(point.dailyActiveUsers);
    const offset: number = previous && Math.abs(previous.y - py) < 16 ? (previous.offset === 0 ? 16 : 0) : 0;
    offsets.set(`${point.label}-${point.dailyActiveUsers}`, offset);
    previous = { y: py, offset };
  }
  return offsets;
}
