/**
 * Product analytics (spec §85).
 *
 * Props are restricted to primitives on purpose: send identifiers and counts, never
 * architecture contents, configuration, prompts or other infrastructure details.
 */
import { logger } from "./logger";

export const ANALYTICS_EVENTS = [
  "project_created",
  "architecture_generated",
  "node_selected",
  "architecture_validated",
  "simulation_started",
  "simulation_completed",
  "capacity_analysis_completed",
  "architecture_change_applied",
  "architecture_change_rejected",
  "discovery_started",
  "discovery_saved",
] as const;

export type AnalyticsEvent = (typeof ANALYTICS_EVENTS)[number];
export type AnalyticsProps = Record<string, string | number | boolean>;
export type AnalyticsSink = (event: AnalyticsEvent, props: AnalyticsProps) => void;

/** INTEGRATION POINT: no analytics provider is chosen yet; events go to the debug log. */
let sink: AnalyticsSink = (event, props) => logger.debug(`analytics: ${event}`, props);

export function setAnalyticsSink(next: AnalyticsSink): void {
  sink = next;
}

export function track(event: AnalyticsEvent, props: AnalyticsProps = {}): void {
  try {
    sink(event, props);
  } catch (error) {
    // Analytics must never break the product.
    logger.warn("Analytics sink failed", { event, error: String(error) });
  }
}
