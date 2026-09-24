"use client";

import { Activity, ArrowRight, CircleCheck, TriangleAlert, Zap } from "lucide-react";
import { useCallback, useMemo } from "react";

import { AnalysisSurface, LocateLink } from "@/components/feedback/AnalysisSurface";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, Td, Th } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { SeverityBadge } from "@/features/validation/severity";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useReliability, useRunReliability } from "@/hooks/use-reliability";
import { formatNumber, formatPercent } from "@/lib/formatting";
import { describeEdge } from "@/lib/graph";
import type { Architecture } from "@/types/architecture";
import type { ReliabilityAnalysis } from "@/types/reliability";

const pct = (value: number) => formatPercent(value, 2);

/** Reliability page container (spec §37, §70). Displays backend reliability results only (spec §111). */
export function ReliabilityView({ projectId }: { projectId: string }) {
  const reliability = useReliability(projectId);
  const architecture = useArchitecture(projectId);
  const { mutate, isPending: running } = useRunReliability(projectId);
  const hasArchitecture = Boolean(architecture.data);

  const runAnalysis = useCallback(() => {
    mutate(undefined, {
      onSuccess: (result) =>
        toast("Reliability analysis complete", {
          tone: "success",
          description: `Analyzed architecture v${result.architectureVersion}.`,
        }),
      onError: (error) => toastError("Reliability analysis failed. No changes were applied.", error),
    });
  }, [mutate]);

  const commands = useMemo(
    () => [
      {
        id: "analysis.reliability.run",
        label: "Analyze reliability",
        group: "Analysis" as const,
        keywords: ["reliability", "availability", "spof", "single point of failure", "cascade"],
        disabled: running || !hasArchitecture,
        run: runAnalysis,
      },
    ],
    [running, hasArchitecture, runAnalysis],
  );
  useRegisterCommands(commands);

  return (
    <AnalysisSurface<ReliabilityAnalysis>
      projectId={projectId}
      title="Reliability"
      description="Availability, single points of failure and cascade risks calculated by the reliability engine."
      icon={Activity}
      engineLabel="Reliability engine"
      query={reliability}
      timestamp={(data) => data.analyzedAt}
      timestampVerb="Analyzed"
      run={{
        onRun: runAnalysis,
        running,
        idleLabel: "Run reliability analysis",
        rerunLabel: "Re-run analysis",
        pendingLabel: "Analyzing…",
      }}
      emptyDescription="Run reliability analysis to estimate availability and find single points of failure, critical paths and cascade risks."
      overlay={{ mode: "reliability", label: "View reliability overlay" }}
    >
      {(data, { architecture: arch, nodeName }) => (
        <ReliabilityResults projectId={projectId} analysis={data} architecture={arch} nodeName={nodeName} />
      )}
    </AnalysisSurface>
  );
}

export interface ReliabilityResultsProps {
  projectId: string;
  analysis: ReliabilityAnalysis;
  architecture: Architecture | null;
  nodeName: (id: string) => string;
}

export function ReliabilityResults({ projectId, analysis, architecture, nodeName }: ReliabilityResultsProps) {
  const { availability } = analysis;
  const belowTarget = availability.target !== null && availability.estimated < availability.target;

  const edgeLabel = (edgeId: string): { label: string; nodeIds: string[] } => {
    const edge = architecture?.edges.find((e) => e.id === edgeId);
    return edge && architecture
      ? { label: describeEdge(architecture, edge), nodeIds: [edge.source, edge.target] }
      : { label: edgeId, nodeIds: [] };
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_320px]">
        <Card role="region" aria-labelledby="availability-heading">
          <CardHeader>
            <CardTitle id="availability-heading">Availability</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent className="flex flex-col gap-4">
            <dl className="grid grid-cols-1 gap-4 sm:grid-cols-3">
              <Stat
                label="Target"
                value={availability.target === null ? "Not set" : pct(availability.target)}
              />
              <Stat label="Estimated" value={pct(availability.estimated)} />
              <Stat
                label="Downtime / month"
                value={formatNumber(availability.monthlyDowntimeMinutes, 0)}
                unit="min"
              />
            </dl>
            {availability.target === null ? (
              <p className="text-sm text-fg-secondary">
                No availability target is set. Add one in requirements to compare against it.
              </p>
            ) : belowTarget ? (
              <p className="flex items-start gap-2 text-sm text-warning-fg">
                <TriangleAlert aria-hidden className="mt-0.5 size-4 shrink-0" />
                Below target: the estimate is under the {pct(availability.target)} target.
              </p>
            ) : (
              <p className="flex items-start gap-2 text-sm text-accent-fg">
                <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0" />
                Meets target of {pct(availability.target)}.
              </p>
            )}
          </CardContent>
        </Card>

        <Card role="region" aria-labelledby="entrypoints-heading">
          <CardHeader>
            <CardTitle id="entrypoints-heading">Entrypoints</CardTitle>
          </CardHeader>
          <CardContent className="p-0">
            {analysis.entrypoints.length > 0 ? (
              <ul className="divide-y divide-default">
                {analysis.entrypoints.map((entry) => (
                  <li
                    key={entry.nodeId}
                    className="flex items-center justify-between gap-3 px-4 py-2 text-sm"
                  >
                    <span className="text-fg">{nodeName(entry.nodeId)}</span>
                    <span className="tabular text-fg-secondary">{pct(entry.availability)}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="px-4 py-3 text-sm text-muted">No entrypoints were identified.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card role="region" aria-labelledby="spof-heading">
        <CardHeader>
          <CardTitle id="spof-heading">Single points of failure</CardTitle>
          <ProvenanceTag kind="finding" />
        </CardHeader>
        <CardContent className="p-0">
          {analysis.singlePointsOfFailure.length > 0 ? (
            <ul className="divide-y divide-default" aria-label="Single points of failure">
              {analysis.singlePointsOfFailure.map((spof) => {
                const name = nodeName(spof.nodeId);
                return (
                  <li key={spof.nodeId} className="flex flex-col gap-2 px-4 py-3">
                    <div className="flex flex-wrap items-center gap-2">
                      <SeverityBadge severity={spof.severity} />
                      <span className="text-sm font-semibold text-fg">{name}</span>
                      <Badge tone="warning">
                        <TriangleAlert aria-hidden />
                        SPOF
                      </Badge>
                    </div>
                    <p className="text-sm text-fg-secondary">{spof.reason}</p>
                    {spof.dependentNodeIds.length > 0 ? (
                      <p className="text-xs text-muted">
                        <span className="font-medium text-fg-secondary">
                          {spof.dependentNodeIds.length} dependent
                          {spof.dependentNodeIds.length === 1 ? "" : "s"}:
                        </span>{" "}
                        {spof.dependentNodeIds.map(nodeName).join(", ")}
                      </p>
                    ) : null}
                    <div className="flex flex-wrap items-center gap-1">
                      <WhyButton evidenceId={spof.evidenceId} subject={`${name} single point of failure`} />
                      <LocateLink
                        projectId={projectId}
                        mode="reliability"
                        nodeIds={[spof.nodeId, ...spof.dependentNodeIds]}
                        subject={name}
                      />
                    </div>
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="flex items-start gap-2 px-4 py-3 text-sm text-fg-secondary">
              <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
              No single points of failure found.
            </p>
          )}
        </CardContent>
      </Card>

      <Card role="region" aria-labelledby="paths-heading">
        <CardHeader>
          <CardTitle id="paths-heading">Critical paths</CardTitle>
          <ProvenanceTag kind="calculated" />
        </CardHeader>
        <CardContent className="p-0">
          {analysis.criticalPaths.length > 0 ? (
            <Table aria-label="Critical paths">
              <thead>
                <tr>
                  <Th>Path</Th>
                  <Th className="text-right">Availability</Th>
                  <Th>
                    <span className="sr-only">Locate</span>
                  </Th>
                </tr>
              </thead>
              <tbody>
                {analysis.criticalPaths.map((path) => {
                  const label = path.nodeIds.map(nodeName).join(" → ");
                  return (
                    <tr key={path.nodeIds.join(">")}>
                      <Td className="min-w-64">{label}</Td>
                      <Td className="tabular text-right whitespace-nowrap">{pct(path.availability)}</Td>
                      <Td className="text-right">
                        <LocateLink
                          projectId={projectId}
                          mode="reliability"
                          nodeIds={path.nodeIds}
                          subject={`path ${label}`}
                        />
                      </Td>
                    </tr>
                  );
                })}
              </tbody>
            </Table>
          ) : (
            <p className="px-4 py-3 text-sm text-muted">No critical paths were identified.</p>
          )}
        </CardContent>
      </Card>

      <Card role="region" aria-labelledby="cascade-heading">
        <CardHeader>
          <CardTitle id="cascade-heading">Cascade risks</CardTitle>
          <ProvenanceTag kind="finding" />
        </CardHeader>
        <CardContent className="p-0">
          {analysis.cascadeRisks.length > 0 ? (
            <ul className="divide-y divide-default" aria-label="Cascade risks">
              {analysis.cascadeRisks.map((risk) => {
                const edge = edgeLabel(risk.edgeId);
                return (
                  <li
                    key={risk.edgeId}
                    className="flex flex-col gap-2 px-4 py-3 sm:flex-row sm:items-start sm:justify-between"
                  >
                    <div className="flex min-w-0 flex-col gap-1">
                      <p className="flex items-center gap-2 text-sm font-semibold text-fg">
                        <Zap aria-hidden className="size-4 shrink-0 text-warning-fg" />
                        {edge.label}
                        <span className="text-xs font-normal text-muted">Critical dependency</span>
                      </p>
                      <ul className="flex flex-col gap-0.5 pl-6 text-sm text-fg-secondary">
                        {risk.reasons.map((reason) => (
                          <li key={reason} className="flex items-start gap-1.5">
                            <ArrowRight aria-hidden className="mt-1 size-3 shrink-0 text-muted" />
                            {reason}
                          </li>
                        ))}
                      </ul>
                    </div>
                    {edge.nodeIds.length > 0 ? (
                      <LocateLink
                        projectId={projectId}
                        mode="reliability"
                        nodeIds={edge.nodeIds}
                        subject={edge.label}
                      />
                    ) : null}
                  </li>
                );
              })}
            </ul>
          ) : (
            <p className="flex items-start gap-2 px-4 py-3 text-sm text-fg-secondary">
              <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
              No cascade risks found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function Stat({ label, value, unit }: { label: string; value: string; unit?: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="label-caps">{label}</dt>
      <dd className="tabular text-2xl font-semibold text-fg">
        {value}
        {unit ? <span className="ml-1 text-xs font-normal text-muted">{unit}</span> : null}
      </dd>
    </div>
  );
}
