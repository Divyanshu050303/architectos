/** Small building blocks shared by the inspector tabs. */
import type { BadgeTone } from "@/components/ui/badge";
import { formatNumber } from "@/lib/formatting";
import type { Severity } from "@/types/validation";

export const SEVERITY_META: Record<Severity, { label: string; tone: BadgeTone }> = {
  critical: { label: "Critical", tone: "danger" },
  high: { label: "High", tone: "danger" },
  medium: { label: "Medium", tone: "warning" },
  low: { label: "Low", tone: "neutral" },
  info: { label: "Info", tone: "info" },
};

export function humanizeKey(key: string): string {
  const spaced = key
    .replace(/[_-]+/g, " ")
    .replace(/([a-z])([A-Z])/g, "$1 $2")
    .trim();
  return spaced.charAt(0).toUpperCase() + spaced.slice(1).toLowerCase();
}

export function formatConfigValue(value: unknown): string {
  if (typeof value === "number") return formatNumber(value);
  if (typeof value === "string") return value || "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (value === null || value === undefined) return "—";
  return JSON.stringify(value);
}

export function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-3 py-1.5 text-sm">
      <dt className="text-fg-secondary">{label}</dt>
      <dd className="min-w-0 truncate text-right text-fg">{children}</dd>
    </div>
  );
}

export function WhyButton({
  evidenceId,
  label,
  onOpen,
}: {
  evidenceId: string;
  label: string;
  onOpen: (id: string) => void;
}) {
  return (
    <button
      type="button"
      onClick={() => onOpen(evidenceId)}
      aria-label={`Why? Evidence for ${label}`}
      className="rounded-sm px-1 text-xs font-medium text-info-fg underline-offset-2 hover:underline"
    >
      Why?
    </button>
  );
}
