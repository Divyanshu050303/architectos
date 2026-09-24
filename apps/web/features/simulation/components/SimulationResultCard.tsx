import { AlertTriangle, ArrowRight, CheckCircle2, Info, OctagonAlert, type LucideIcon } from "lucide-react";

import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { formatNumber, formatPercent } from "@/lib/formatting";
import type { SimulationImpact, SimulationMetric, SimulationResult, SimulationRun } from "@/types/simulation";

import { durationLabel, ENVIRONMENT_LABELS, TRAFFIC_LABELS } from "./SimulationForm";

interface Tone {
  label: string;
  tone: BadgeTone;
  Icon: LucideIcon;
}

const IMPACT_META: Record<SimulationImpact, Tone> = {
  low: { label: "Low", tone: "neutral", Icon: Info },
  medium: { label: "Medium", tone: "warning", Icon: AlertTriangle },
  high: { label: "High", tone: "danger", Icon: OctagonAlert },
  critical: { label: "Critical", tone: "danger", Icon: OctagonAlert },
};

const CASCADE_META: Record<SimulationResult["cascadingFailure"], Tone> = {
  none: { label: "None", tone: "neutral", Icon: CheckCircle2 },
  potential: { label: "Potential", tone: "warning", Icon: AlertTriangle },
  likely: { label: "Likely", tone: "danger", Icon: OctagonAlert },
};

function ToneBadge({ meta, className }: { meta: Tone; className?: string }) {
  return (
    <Badge tone={meta.tone} className={className}>
      <meta.Icon aria-hidden />
      {meta.label}
    </Badge>
  );
}

/** Display formatting of an engine value: 1800 ms → "1.8s", 3590 /min → "3,590/min". */
export function formatMetricValue(value: number, unit: string): string {
  if (unit === "ms")
    return value >= 1000 ? `${formatNumber(value / 1000, 1)}s` : `${formatNumber(value, 0)}ms`;
  if (unit.startsWith("/") || unit === "s" || unit === "%") return `${formatNumber(value)}${unit}`;
  return unit ? `${formatNumber(value)} ${unit}` : formatNumber(value);
}

function BeforeAfter({ label, before, after }: { label: string; before: string; after: string }) {
  return (
    <div className="flex flex-col gap-1 rounded-sm border border-default bg-surface-2 px-3 py-2">
      <dt className="label-caps">{label}</dt>
      <dd className="tabular flex flex-wrap items-center gap-1.5 text-base font-semibold text-fg">
        <span className="text-fg-secondary">{before}</span>
        <ArrowRight aria-hidden className="size-3.5 text-muted" />
        <span className="sr-only">to</span>
        <span>{after}</span>
      </dd>
    </div>
  );
}

/** P99 and error rate first, as in the spec layout; other metrics after, in the engine's order. */
const isP99 = (m: SimulationMetric) => /p99/i.test(m.metric);

export interface SimulationResultCardProps {
  run: SimulationRun;
  result: SimulationResult;
  scenarioLabel: string;
  nodeName: (id: string) => string;
  actions?: React.ReactNode;
}

/** SIMULATION RESULT (spec §40). Every number is simulation-engine output, shown as-is. */
export function SimulationResultCard({
  run,
  result,
  scenarioLabel,
  nodeName,
  actions,
}: SimulationResultCardProps) {
  const { config } = run;
  const p99 = result.metrics.filter(isP99);
  const others = result.metrics.filter((m) => !isP99(m));
  return (
    <Card role="region" aria-labelledby="simulation-result-heading">
      <CardHeader className="flex-wrap">
        <div className="flex items-center gap-2">
          <CardTitle id="simulation-result-heading">Simulation result</CardTitle>
          <ProvenanceTag kind="calculated" label="Simulation engine" />
        </div>
        <WhyButton evidenceId={result.evidenceIds[0] ?? null} subject="simulation result" />
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <p className="text-sm text-fg-secondary">
          <span className="font-medium text-fg">{scenarioLabel}</span>
          <span aria-hidden> · </span>
          <span className="sr-only">, </span>
          {TRAFFIC_LABELS[config.traffic]} traffic
          <span aria-hidden> · </span>
          <span className="sr-only">, </span>
          {durationLabel(config.durationMinutes)}
          <span aria-hidden> · </span>
          <span className="sr-only">, </span>
          {ENVIRONMENT_LABELS[config.environment]}
          <span aria-hidden> · </span>
          <span className="sr-only">, </span>
          architecture <span className="tabular">v{run.architectureVersion}</span>
        </p>

        <dl className="grid grid-cols-1 gap-3 sm:grid-cols-2">
          <div className="flex flex-col gap-1.5 rounded-sm border border-default px-3 py-2">
            <dt className="label-caps">Impact</dt>
            <dd>
              <ToneBadge meta={IMPACT_META[result.impact]} className="h-6 px-2.5 text-xs" />
            </dd>
          </div>
          <div className="flex flex-col gap-1.5 rounded-sm border border-default px-3 py-2">
            <dt className="label-caps">Cascading failure</dt>
            <dd>
              <ToneBadge meta={CASCADE_META[result.cascadingFailure]} className="h-6 px-2.5 text-xs" />
            </dd>
          </div>
          {p99.map((m) => (
            <BeforeAfter
              key={m.metric}
              label={m.metric.replace(/ latency$/i, "")}
              before={formatMetricValue(m.before, m.unit)}
              after={formatMetricValue(m.after, m.unit)}
            />
          ))}
          <BeforeAfter
            label="Error rate"
            before={formatPercent(result.errorRate.before, 1)}
            after={formatPercent(result.errorRate.after, 1)}
          />
          {others.map((m) => (
            <BeforeAfter
              key={m.metric}
              label={m.metric.replace(/ latency$/i, "")}
              before={formatMetricValue(m.before, m.unit)}
              after={formatMetricValue(m.after, m.unit)}
            />
          ))}
        </dl>

        <section aria-labelledby="simulation-affected-heading" className="flex flex-col gap-2">
          <h3 id="simulation-affected-heading" className="label-caps">
            Affected components <span className="tabular">({result.affectedNodeIds.length})</span>
          </h3>
          {result.affectedNodeIds.length === 0 ? (
            <p className="text-sm text-fg-secondary">No components were affected.</p>
          ) : (
            <ul className="flex flex-wrap gap-1.5">
              {result.affectedNodeIds.map((id) => (
                <li
                  key={id}
                  className="rounded-sm border border-default bg-surface-2 px-2 py-0.5 text-sm text-fg"
                >
                  {nodeName(id)}
                </li>
              ))}
            </ul>
          )}
        </section>

        {actions ? <div className="flex flex-wrap gap-2">{actions}</div> : null}
      </CardContent>
    </Card>
  );
}
