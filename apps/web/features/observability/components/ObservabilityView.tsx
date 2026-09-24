"use client";

import { CircleCheck, Eye, TriangleAlert } from "lucide-react";
import { useCallback, useMemo } from "react";

import { AnalysisSurface, LocateLink, ScoreStat, TriStateCell } from "@/components/feedback/AnalysisSurface";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter, type MeterTone } from "@/components/ui/progress";
import { Table, Td, Th } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useObservability, useRunObservability } from "@/hooks/use-observability";
import { formatPercent } from "@/lib/formatting";
import { TELEMETRY_SIGNALS } from "@/schemas/observability";
import type { ObservabilityAnalysis, Slo, TelemetrySignal } from "@/types/observability";

const SIGNAL_LABEL: Record<TelemetrySignal, string> = {
  metrics: "Metrics",
  logs: "Logs",
  traces: "Traces",
  alerts: "Alerts",
  dashboards: "Dashboards",
};

/** Observability page container (spec §37, §68). Displays backend results only (spec §111). */
export function ObservabilityView({ projectId }: { projectId: string }) {
  const observability = useObservability(projectId);
  const architecture = useArchitecture(projectId);
  const { mutate, isPending: running } = useRunObservability(projectId);
  const hasArchitecture = Boolean(architecture.data);

  const runAnalysis = useCallback(() => {
    mutate(undefined, {
      onSuccess: (result) =>
        toast("Observability analysis complete", {
          tone: "success",
          description: `Analyzed architecture v${result.architectureVersion}.`,
        }),
      onError: (error) => toastError("Observability analysis failed. No changes were applied.", error),
    });
  }, [mutate]);

  const commands = useMemo(
    () => [
      {
        id: "analysis.observability.run",
        label: "Analyze observability",
        group: "Analysis" as const,
        keywords: ["observability", "telemetry", "slo", "metrics", "logs", "traces", "alerts"],
        disabled: running || !hasArchitecture,
        run: runAnalysis,
      },
    ],
    [running, hasArchitecture, runAnalysis],
  );
  useRegisterCommands(commands);

  return (
    <AnalysisSurface<ObservabilityAnalysis>
      projectId={projectId}
      title="Observability"
      description="Telemetry coverage, SLOs and gaps reported by the observability analysis."
      icon={Eye}
      engineLabel="Observability analysis"
      query={observability}
      timestamp={(data) => data.analyzedAt}
      timestampVerb="Analyzed"
      run={{
        onRun: runAnalysis,
        running,
        idleLabel: "Run observability analysis",
        rerunLabel: "Re-run analysis",
        pendingLabel: "Analyzing…",
      }}
      emptyDescription="Run observability analysis to check metrics, logs, traces, alerts and dashboards per component and track SLOs."
      overlay={{ mode: "observability", label: "View observability overlay" }}
    >
      {(data, { nodeName }) => (
        <ObservabilityResults projectId={projectId} analysis={data} nodeName={nodeName} />
      )}
    </AnalysisSurface>
  );
}

function budgetTone(remaining: number): MeterTone {
  if (remaining <= 0) return "danger";
  if (remaining < 0.25) return "warning";
  return "accent";
}

export interface ObservabilityResultsProps {
  projectId: string;
  analysis: ObservabilityAnalysis;
  nodeName: (id: string) => string;
}

export function ObservabilityResults({ projectId, analysis, nodeName }: ObservabilityResultsProps) {
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <Card role="region" aria-labelledby="obs-score-heading">
          <CardHeader>
            <CardTitle id="obs-score-heading">Score</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent>
            <ScoreStat
              score={analysis.score}
              label="Observability score"
              caption={`Based on ${analysis.coverage.length} components and ${analysis.gaps.length} ${analysis.gaps.length === 1 ? "gap" : "gaps"}.`}
            />
          </CardContent>
        </Card>

        <Card role="region" aria-labelledby="slo-heading">
          <CardHeader>
            <CardTitle id="slo-heading">Service level objectives</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent className="p-0">
            {analysis.slos.length > 0 ? (
              <ul className="divide-y divide-default" aria-label="Service level objectives">
                {analysis.slos.map((slo) => (
                  <SloRow key={slo.id} slo={slo} nodeName={nodeName} />
                ))}
              </ul>
            ) : (
              <p className="px-4 py-3 text-sm text-muted">
                No SLOs are defined. Add availability or latency targets in requirements.
              </p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card role="region" aria-labelledby="coverage-heading">
        <CardHeader>
          <CardTitle id="coverage-heading">Coverage</CardTitle>
          <ProvenanceTag kind="calculated" />
        </CardHeader>
        <CardContent className="p-0">
          {analysis.coverage.length > 0 ? (
            <Table aria-label="Telemetry coverage by component">
              <thead>
                <tr>
                  <Th>Component</Th>
                  {TELEMETRY_SIGNALS.map((signal) => (
                    <Th key={signal}>{SIGNAL_LABEL[signal]}</Th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {analysis.coverage.map((row) => {
                  const name = nodeName(row.nodeId);
                  return (
                    <tr key={row.nodeId}>
                      <th
                        scope="row"
                        className="border-b border-default px-3 py-2 text-left font-normal whitespace-nowrap text-fg"
                      >
                        {name}
                      </th>
                      {TELEMETRY_SIGNALS.map((signal) => (
                        <Td key={signal} className="whitespace-nowrap">
                          <TriStateCell
                            value={row[signal]}
                            label={`${name} ${signal}`}
                            yes="Covered"
                            no="Missing"
                          />
                        </Td>
                      ))}
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          ) : (
            <p className="px-4 py-3 text-sm text-muted">No component coverage was reported.</p>
          )}
        </CardContent>
      </Card>

      <Card role="region" aria-labelledby="gaps-heading">
        <CardHeader>
          <CardTitle id="gaps-heading">Gaps</CardTitle>
          <ProvenanceTag kind="finding" />
        </CardHeader>
        <CardContent className="p-0">
          {analysis.gaps.length > 0 ? (
            <ul className="divide-y divide-default" aria-label="Observability gaps">
              {analysis.gaps.map((gap) => {
                const name = nodeName(gap.nodeId);
                return (
                  <li
                    key={gap.nodeId}
                    className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between"
                  >
                    <div className="flex min-w-0 flex-col gap-1.5">
                      <p className="flex items-center gap-2 text-sm font-semibold text-fg">
                        <TriangleAlert aria-hidden className="size-4 shrink-0 text-warning-fg" />
                        {name}
                      </p>
                      <div className="flex flex-wrap items-center gap-1.5">
                        <span className="text-xs text-muted">Missing:</span>
                        {gap.missing.map((signal) => (
                          <Badge key={signal} tone="warning">
                            {SIGNAL_LABEL[signal]}
                          </Badge>
                        ))}
                      </div>
                      <p className="text-sm text-fg-secondary">{gap.recommendation}</p>
                    </div>
                    <LocateLink
                      projectId={projectId}
                      mode="observability"
                      nodeIds={[gap.nodeId]}
                      subject={name}
                    />
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="flex items-start gap-2 px-4 py-3 text-sm text-fg-secondary">
              <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
              Every component has full telemetry coverage.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function SloRow({ slo, nodeName }: { slo: Slo; nodeName: (id: string) => string }) {
  const meeting = slo.current >= slo.target;
  return (
    <li className="flex flex-col gap-2 px-4 py-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <span className="text-sm font-semibold text-fg">{slo.name}</span>
        {meeting ? (
          <Badge tone="accent">
            <CircleCheck aria-hidden />
            Meeting target
          </Badge>
        ) : (
          <Badge tone="danger">
            <TriangleAlert aria-hidden />
            Below target
          </Badge>
        )}
      </div>
      <p className="tabular text-xs text-fg-secondary">
        Target {formatPercent(slo.target, 2)} · Current {formatPercent(slo.current, 2)} · Window {slo.window}
      </p>
      <div className="flex items-center gap-2">
        <span className="text-xs text-muted">Error budget</span>
        <Meter
          value={slo.errorBudgetRemaining}
          tone={budgetTone(slo.errorBudgetRemaining)}
          label={`${slo.name} error budget remaining`}
          className="flex-1"
        />
        <span className="tabular w-20 text-right text-xs text-fg">
          {formatPercent(slo.errorBudgetRemaining)} left
        </span>
      </div>
      <p className="text-xs text-muted">{slo.nodeIds.map(nodeName).join(", ")}</p>
    </li>
  );
}
