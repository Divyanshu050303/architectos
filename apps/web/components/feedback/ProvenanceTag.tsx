import { AlertTriangle, Calculator, FileSearch, FileText, type LucideIcon } from "lucide-react";

import { cn } from "@/lib/utils";

/**
 * Where a piece of information came from (spec §123–124): what the system IS (fact),
 * THINKS (ai), CALCULATED (calculated), FOUND (finding), and the evidence behind it.
 */
export type ProvenanceKind = "fact" | "ai" | "calculated" | "finding" | "evidence";

const KIND_META: Record<ProvenanceKind, { label: string; Icon: LucideIcon | null; className: string }> = {
  fact: { label: "User data", Icon: FileText, className: "border-default bg-surface-2 text-fg-secondary" },
  // AI uses the ✦ mark, not an icon (spec §102)
  ai: { label: "AI proposal", Icon: null, className: "border-accent/40 bg-accent-soft text-accent-fg" },
  calculated: {
    label: "Calculated",
    Icon: Calculator,
    className: "border-info/40 bg-info-soft text-info-fg",
  },
  finding: {
    label: "Finding",
    Icon: AlertTriangle,
    className: "border-warning/40 bg-warning-soft text-warning-fg",
  },
  evidence: { label: "Evidence", Icon: FileSearch, className: "border-default bg-sunken text-fg-secondary" },
};

export interface ProvenanceTagProps {
  kind: ProvenanceKind;
  /** Overrides the default label, e.g. "Capacity engine". */
  label?: string;
  className?: string;
}

export function ProvenanceTag({ kind, label, className }: ProvenanceTagProps) {
  const meta = KIND_META[kind];
  return (
    <span
      className={cn(
        "inline-flex h-5 items-center gap-1 rounded-sm border px-1.5 text-2xs font-medium whitespace-nowrap",
        meta.className,
        className,
      )}
    >
      {meta.Icon ? (
        <meta.Icon aria-hidden className="size-3" />
      ) : (
        <span aria-hidden className="leading-none">
          ✦
        </span>
      )}
      {label ?? meta.label}
    </span>
  );
}
