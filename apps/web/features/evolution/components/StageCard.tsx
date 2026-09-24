import { ArrowUpRight, Minus, Plus } from "lucide-react";
import Link from "next/link";

import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge } from "@/components/ui/badge";
import { projectHref } from "@/config/navigation";
import { formatCompact, formatCurrency } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type { EvolutionChange, EvolutionStage } from "@/types/evolution";

import { STAGE_STATUS_META } from "./EvolutionTimeline";
import { RiskBadge } from "./RiskBadge";

const CHANGE_META: Record<
  EvolutionChange["kind"],
  { srLabel: string; symbol: React.ReactNode; className: string }
> = {
  add: { srLabel: "Add", symbol: <Plus className="size-3.5" />, className: "text-accent-fg" },
  remove: { srLabel: "Remove", symbol: <Minus className="size-3.5" />, className: "text-danger-fg" },
  change: { srLabel: "Change", symbol: "~", className: "text-warning-fg" },
};

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="grid grid-cols-1 gap-1 border-b border-default py-3 last:border-b-0 sm:grid-cols-[140px_minmax(0,1fr)] sm:gap-4">
      <dt className="label-caps pt-0.5">{label}</dt>
      <dd className="min-w-0 text-sm text-fg">{children}</dd>
    </div>
  );
}

export interface StageCardProps {
  projectId: string;
  stage: EvolutionStage;
  currency: string;
}

/** Architecture · Trigger · Changes · Cost · Risk · Migration for one stage (spec §42). */
export function StageCard({ projectId, stage, currency }: StageCardProps) {
  const status = STAGE_STATUS_META[stage.status];
  const architectureHref =
    stage.status === "current"
      ? projectHref(projectId, "architecture")
      : `${projectHref(projectId, "architecture")}?version=${stage.architectureVersion ?? ""}`;

  return (
    <div className="flex flex-col">
      <div className="flex flex-wrap items-center gap-2 pb-2">
        <h2 className="text-base font-semibold text-fg">
          {stage.label}{" "}
          <span className="tabular font-normal text-fg-secondary">
            · {formatCompact(stage.dailyActiveUsers)} DAU
          </span>
        </h2>
        <Badge tone={stage.status === "current" ? "info" : "neutral"}>
          <status.Icon aria-hidden />
          {status.label}
        </Badge>
      </div>

      <dl className="flex flex-col">
        <Row label="Architecture">
          {stage.architectureVersion !== null ? (
            <Link
              href={architectureHref}
              className="inline-flex items-center gap-1 font-medium text-fg underline decoration-strong underline-offset-4 hover:decoration-fg"
            >
              <span className="tabular">v{stage.architectureVersion}</span>
              {stage.status === "current" ? <span className="text-fg-secondary">(current)</span> : null}
              <ArrowUpRight aria-hidden className="size-3.5" />
            </Link>
          ) : (
            <span className="text-fg-secondary">Planned — no saved version yet</span>
          )}
        </Row>
        <Row label="Trigger">{stage.trigger}</Row>
        <Row label="Changes">
          {stage.changes.length === 0 ? (
            <span className="text-fg-secondary">No changes recorded.</span>
          ) : (
            <ul className="flex flex-col gap-1">
              {stage.changes.map((change, i) => {
                const meta = CHANGE_META[change.kind];
                return (
                  <li key={`${change.kind}-${i}`} className="flex items-start gap-2">
                    <span
                      aria-hidden
                      className={cn(
                        "tabular mt-0.5 flex size-4 shrink-0 items-center justify-center font-semibold leading-none",
                        meta.className,
                      )}
                    >
                      {meta.symbol}
                    </span>
                    <span>
                      <span className="sr-only">{meta.srLabel}: </span>
                      {change.description}
                    </span>
                  </li>
                );
              })}
            </ul>
          )}
        </Row>
        <Row label="Capacity">
          <span className="flex flex-wrap items-center gap-2">
            <span>
              Supports up to{" "}
              <span className="tabular font-medium">{formatCompact(stage.maxSupportedDailyActiveUsers)}</span>{" "}
              DAU
            </span>
            <ProvenanceTag kind="calculated" />
          </span>
        </Row>
        <Row label="Cost">
          <span className="flex flex-wrap items-center gap-2">
            <span>
              <span className="tabular font-medium">{formatCurrency(stage.monthlyCost, currency)}</span>
              <span className="text-fg-secondary">/month</span>
            </span>
            <ProvenanceTag kind="calculated" />
          </span>
        </Row>
        <Row label="Risk">
          <RiskBadge risk={stage.risk} />
        </Row>
        <Row label="Migration">
          {stage.migrationId ? (
            <Link
              href={`${projectHref(projectId, "migration")}?migration=${encodeURIComponent(stage.migrationId)}`}
              className="inline-flex items-center gap-1 font-medium text-fg underline decoration-strong underline-offset-4 hover:decoration-fg"
            >
              View migration plan to {stage.label}
              <ArrowUpRight aria-hidden className="size-3.5" />
            </Link>
          ) : (
            <span className="text-fg-secondary">
              {stage.status === "planned"
                ? "No migration plan yet."
                : "No migration plan tracked for this stage."}
            </span>
          )}
        </Row>
      </dl>
    </div>
  );
}
