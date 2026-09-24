/**
 * Presentation metadata for drift items (spec §45). Status is always shown as
 * icon + text + colour, never colour alone (spec §62). Statuses come from the backend
 * drift checker; nothing here decides whether something drifted.
 */
import { CheckCircle2, CircleHelp, GitCompareArrows, type LucideIcon, SearchX } from "lucide-react";

import type { BadgeTone } from "@/components/ui/badge";
import type { DriftStatus } from "@/types/discovery";

export interface DriftStatusMeta {
  label: string;
  tone: BadgeTone;
  Icon: LucideIcon;
  /** Highlight for the ACTUAL cell when it differs from EXPECTED. */
  actualClass: string;
  /** One-line explanation for screen readers and tooltips. */
  description: string;
}

export const DRIFT_STATUS_META: Record<DriftStatus, DriftStatusMeta> = {
  drifted: {
    label: "Drifted",
    tone: "warning",
    Icon: GitCompareArrows,
    actualClass: "bg-warning-soft text-warning-fg font-semibold",
    description: "Deployed value differs from the architecture.",
  },
  missing: {
    label: "Missing",
    tone: "danger",
    Icon: SearchX,
    actualClass: "bg-danger-soft text-danger-fg font-semibold",
    description: "In the architecture but not found in the infrastructure.",
  },
  unexpected: {
    label: "Unexpected",
    tone: "info",
    Icon: CircleHelp,
    actualClass: "bg-info-soft text-info-fg font-semibold",
    description: "Found in the infrastructure but not in the architecture.",
  },
  matching: {
    label: "Matching",
    tone: "accent",
    Icon: CheckCircle2,
    actualClass: "text-fg",
    description: "Deployed value matches the architecture.",
  },
};

export type DriftFilter = "drifted" | "all";

export function isDriftFilter(value: string | null): value is DriftFilter {
  return value === "drifted" || value === "all";
}
