/**
 * What the canvas chrome says about the active analysis overlay (spec §68–72): a
 * summary figure when the backend analysis exists, a hint when it does not, and
 * whether the overlay's analysis for the shown version is still loading (spec §25).
 * Pure: every figure is backend data, only formatted here.
 */
import { formatCurrency, formatPercent } from "@/lib/formatting";
import type { AnalysisMode } from "@/types/architecture";
import type { CapacityAnalysis } from "@/types/capacity";
import type { CostEstimate } from "@/types/cost";
import type { ObservabilityAnalysis } from "@/types/observability";
import type { ReliabilityAnalysis } from "@/types/reliability";
import type { SecurityAnalysis } from "@/types/security";
import type { SimulationRun } from "@/types/simulation";
import type { ValidationReport } from "@/types/validation";

import type { OverlayHintProps, OverlaySummaryProps } from "../components/OverlayChrome";

/** Analyses whose queries feed the overlays. */
export type OverlaySource =
  "capacity" | "validation" | "reliability" | "security" | "cost" | "observability" | "simulation";

export type OverlayFlags = Record<OverlaySource, boolean>;

/** The analyses each overlay's node statuses are built from. */
const MODE_SOURCES: Record<AnalysisMode, readonly OverlaySource[]> = {
  topology: ["capacity", "validation"],
  capacity: ["capacity", "validation"],
  reliability: ["reliability", "validation"],
  security: ["security"],
  cost: ["cost"],
  observability: ["observability"],
  simulation: ["simulation"],
};

/** True while any analysis behind the active overlay is still fetching for the shown version. */
export function isOverlayLoading(mode: AnalysisMode, fetching: OverlayFlags): boolean {
  return MODE_SOURCES[mode].some((source) => fetching[source]);
}

/**
 * A query is "loading for this version" on its first fetch, or while it refetches
 * data computed for another version (e.g. right after a save).
 */
export function fetchingForVersion(
  query: {
    isLoading: boolean;
    isFetching: boolean;
    data?: { architectureVersion: number } | null | undefined;
  },
  version: number | null,
): boolean {
  if (query.isLoading) return true;
  const dataVersion = query.data?.architectureVersion;
  return query.isFetching && version !== null && dataVersion !== undefined && dataVersion !== version;
}

export interface OverlayChromeInput {
  mode: AnalysisMode;
  /** `/project/:id/:segment` for the analysis pages. */
  page: (segment: string) => string;
  capacity: CapacityAnalysis | null;
  validation: ValidationReport | null;
  reliability: ReliabilityAnalysis | null;
  security: SecurityAnalysis | null;
  cost: CostEstimate | null;
  observability: ObservabilityAnalysis | null;
  run: SimulationRun | null;
  hasSimulationResult: boolean;
  /** First load of each analysis still pending (no hint until we know it is missing). */
  pending: OverlayFlags;
}

export interface OverlayChrome {
  hint: OverlayHintProps | null;
  summary: OverlaySummaryProps | null;
}

export function overlayChrome(input: OverlayChromeInput): OverlayChrome {
  const { mode, page, pending } = input;
  switch (mode) {
    case "capacity":
      return {
        hint:
          !input.capacity && !pending.capacity
            ? { text: "Capacity has not been analyzed for this version.", href: page("capacity") }
            : null,
        summary: null,
      };
    case "reliability": {
      const { reliability } = input;
      if (reliability) {
        const { estimated, target } = reliability.availability;
        return {
          hint: null,
          summary: {
            label: "Availability",
            value: formatPercent(estimated, 2),
            detail: target !== null ? `target ${formatPercent(target, 2)}` : undefined,
            href: page("reliability"),
          },
        };
      }
      if (pending.reliability) return { hint: null, summary: null };
      return {
        hint: input.validation
          ? {
              text: "Reliability not analyzed; showing validation findings.",
              href: page("reliability"),
              action: "Analyze",
            }
          : { text: "Reliability has not been analyzed for this version.", href: page("reliability") },
        summary: null,
      };
    }
    case "security":
      if (input.security) {
        return {
          hint: null,
          summary: { label: "Security score", value: `${input.security.score}/100`, href: page("security") },
        };
      }
      return {
        hint: pending.security
          ? null
          : { text: "Security has not been analyzed for this version.", href: page("security") },
        summary: null,
      };
    case "cost":
      if (input.cost) {
        return {
          hint: null,
          summary: {
            label: "Total",
            value: `${formatCurrency(input.cost.total, input.cost.currency)}/mo`,
            href: page("cost"),
          },
        };
      }
      return {
        hint: pending.cost
          ? null
          : { text: "Cost has not been estimated for this version.", href: page("cost") },
        summary: null,
      };
    case "observability":
      if (input.observability) {
        return {
          hint: null,
          summary: {
            label: "Observability score",
            value: `${input.observability.score}/100`,
            href: page("observability"),
          },
        };
      }
      return {
        hint: pending.observability
          ? null
          : { text: "Observability has not been analyzed for this version.", href: page("observability") },
        summary: null,
      };
    case "simulation":
      return { hint: simulationHint(input), summary: null };
    case "topology":
      return { hint: null, summary: null };
  }
}

function simulationHint({
  run,
  hasSimulationResult,
  pending,
  page,
}: OverlayChromeInput): OverlayHintProps | null {
  if (pending.simulation) return null;
  if (!run) {
    return { text: "No simulation has been run yet.", href: page("simulation"), action: "Run a simulation" };
  }
  if (run.status === "queued" || run.status === "running") {
    return { text: "The simulation is still running.", href: page("simulation"), action: "View progress" };
  }
  if (!hasSimulationResult) {
    return { text: "The latest simulation did not finish.", href: page("simulation"), action: "Open" };
  }
  return null;
}
