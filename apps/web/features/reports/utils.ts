import type { ProvenanceKind } from "@/components/feedback/ProvenanceTag";
import type { Assumption } from "@/types/architecture";

/** Display helpers for the printable architecture report. No analysis happens here. */

export const NOT_ANALYZED = "Not analyzed yet.";

export const ASSUMPTION_SOURCE: Record<Assumption["source"], { kind: ProvenanceKind; label: string }> = {
  user: { kind: "fact", label: "User" },
  ai: { kind: "ai", label: "AI assumption" },
  default: { kind: "fact", label: "Default" },
};

export function humanize(value: string): string {
  const text = value.replaceAll("_", " ");
  return text.charAt(0).toUpperCase() + text.slice(1);
}

export function replicas(configuration: Record<string, unknown>): string {
  const value = configuration.replicas;
  return typeof value === "number" || typeof value === "string" ? String(value) : "—";
}
