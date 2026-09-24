/**
 * Presentation metadata for findings. Severity is always shown as icon + text + colour,
 * never colour alone (spec §62).
 */
import {
  CircleAlert,
  CircleArrowDown,
  Info,
  type LucideIcon,
  OctagonAlert,
  TriangleAlert,
} from "lucide-react";

import { HEALTH_CATEGORIES, SEVERITIES } from "@/schemas/validation";
import type { Finding, HealthCategory, Severity } from "@/types/validation";

export interface SeverityMeta {
  label: string;
  Icon: LucideIcon;
  /** Badge / pill classes. */
  badgeClass: string;
  /** Icon colour when shown inline next to a title. */
  iconClass: string;
}

export const SEVERITY_META: Record<Severity, SeverityMeta> = {
  critical: {
    label: "Critical",
    Icon: OctagonAlert,
    badgeClass: "bg-danger-soft text-danger-fg border-danger/40",
    iconClass: "text-danger-fg",
  },
  high: {
    label: "High",
    Icon: TriangleAlert,
    badgeClass: "bg-warning-soft text-warning-fg border-warning/40",
    iconClass: "text-warning-fg",
  },
  medium: {
    label: "Medium",
    Icon: CircleAlert,
    badgeClass: "bg-warning-soft/50 text-fg-secondary border-default",
    iconClass: "text-warning-fg",
  },
  low: {
    label: "Low",
    Icon: CircleArrowDown,
    badgeClass: "bg-surface-2 text-fg-secondary border-default",
    iconClass: "text-muted",
  },
  info: {
    label: "Info",
    Icon: Info,
    badgeClass: "bg-info-soft text-info-fg border-info/40",
    iconClass: "text-info-fg",
  },
};

export const CATEGORY_LABEL: Record<HealthCategory, string> = {
  capacity: "Capacity",
  reliability: "Reliability",
  security: "Security",
  observability: "Observability",
  cost: "Cost",
};

export function isSeverity(value: string): value is Severity {
  return (SEVERITIES as readonly string[]).includes(value);
}

export function isHealthCategory(value: string): value is HealthCategory {
  return (HEALTH_CATEGORIES as readonly string[]).includes(value);
}

/** "1 finding", "4 findings". */
export function pluralFindings(count: number): string {
  return `${count} ${count === 1 ? "finding" : "findings"}`;
}

/** The most severe severity among `findings`, or null when there are none. */
export function worstSeverity(findings: readonly Pick<Finding, "severity">[]): Severity | null {
  for (const severity of SEVERITIES) {
    if (findings.some((f) => f.severity === severity)) return severity;
  }
  return null;
}

export function SeverityBadge({ severity, className }: { severity: Severity; className?: string }) {
  const meta = SEVERITY_META[severity];
  return (
    <span
      className={[
        "inline-flex h-5 items-center gap-1 rounded-full border px-2 text-2xs font-medium whitespace-nowrap",
        meta.badgeClass,
        className,
      ]
        .filter(Boolean)
        .join(" ")}
    >
      <meta.Icon aria-hidden className="size-3" />
      {meta.label}
    </span>
  );
}
