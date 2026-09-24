"use client";

import { CircleCheck, Globe, Lock, Network, ShieldHalf, UserRound } from "lucide-react";
import { useCallback, useMemo } from "react";

import { AnalysisSurface, LocateLink, ScoreStat, TriStateCell } from "@/components/feedback/AnalysisSurface";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, Td, Th } from "@/components/ui/table";
import { toast } from "@/components/ui/toast";
import { WhyButton } from "@/features/evidence/components/WhyButton";
import { SeverityBadge } from "@/features/validation/severity";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useRunSecurity, useSecurity } from "@/hooks/use-security";
import type { ExposureLevel, SecurityAnalysis, SecurityControls, StrideCategory } from "@/types/security";

export const STRIDE_LABEL: Record<StrideCategory, string> = {
  spoofing: "Spoofing",
  tampering: "Tampering",
  repudiation: "Repudiation",
  information_disclosure: "Information disclosure",
  denial_of_service: "Denial of service",
  elevation_of_privilege: "Elevation of privilege",
};

const EXPOSURE_META: Record<ExposureLevel, { label: string; tone: BadgeTone; Icon: typeof Globe }> = {
  public: { label: "Public", tone: "warning", Icon: Globe },
  internal: { label: "Internal", tone: "info", Icon: Network },
  private: { label: "Private", tone: "neutral", Icon: Lock },
};

const CONTROL_COLUMNS: ReadonlyArray<{
  key: Exclude<keyof SecurityControls, "nodeId" | "handlesPii">;
  label: string;
  long: string;
}> = [
  { key: "authentication", label: "AuthN", long: "authentication" },
  { key: "authorization", label: "AuthZ", long: "authorization" },
  { key: "encryptionInTransit", label: "TLS", long: "encryption in transit" },
  { key: "encryptionAtRest", label: "At rest", long: "encryption at rest" },
  { key: "secretsManagement", label: "Secrets", long: "secrets management" },
];

/** Security page container (spec §37, §68). Displays backend security results only (spec §111). */
export function SecurityView({ projectId }: { projectId: string }) {
  const security = useSecurity(projectId);
  const architecture = useArchitecture(projectId);
  const { mutate, isPending: running } = useRunSecurity(projectId);
  const hasArchitecture = Boolean(architecture.data);

  const runAnalysis = useCallback(() => {
    mutate(undefined, {
      onSuccess: (result) =>
        toast("Security analysis complete", {
          tone: "success",
          description: `Analyzed architecture v${result.architectureVersion}.`,
        }),
      onError: (error) => toastError("Security analysis failed. No changes were applied.", error),
    });
  }, [mutate]);

  const commands = useMemo(
    () => [
      {
        id: "analysis.security.run",
        label: "Analyze security",
        group: "Analysis" as const,
        keywords: ["security", "threats", "stride", "exposure", "controls", "trust boundary"],
        disabled: running || !hasArchitecture,
        run: runAnalysis,
      },
    ],
    [running, hasArchitecture, runAnalysis],
  );
  useRegisterCommands(commands);

  return (
    <AnalysisSurface<SecurityAnalysis>
      projectId={projectId}
      title="Security"
      description="Trust boundaries, exposure, controls and STRIDE threats found by the security rules."
      icon={ShieldHalf}
      engineLabel="Security rules"
      query={security}
      timestamp={(data) => data.analyzedAt}
      timestampVerb="Analyzed"
      run={{
        onRun: runAnalysis,
        running,
        idleLabel: "Run security analysis",
        rerunLabel: "Re-run analysis",
        pendingLabel: "Analyzing…",
      }}
      emptyDescription="Run security analysis to map trust boundaries and exposure, check controls per component and list STRIDE threats."
      overlay={{ mode: "security", label: "View security overlay" }}
    >
      {(data, { nodeName }) => <SecurityResults projectId={projectId} analysis={data} nodeName={nodeName} />}
    </AnalysisSurface>
  );
}

export interface SecurityResultsProps {
  projectId: string;
  analysis: SecurityAnalysis;
  nodeName: (id: string) => string;
}

export function SecurityResults({ projectId, analysis, nodeName }: SecurityResultsProps) {
  return (
    <div className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[240px_minmax(0,1fr)]">
        <Card role="region" aria-labelledby="security-score-heading">
          <CardHeader>
            <CardTitle id="security-score-heading">Score</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent>
            <ScoreStat
              score={analysis.score}
              label="Security score"
              caption={`Based on ${analysis.threats.length} ${analysis.threats.length === 1 ? "threat" : "threats"} and ${analysis.controls.length} component controls.`}
            />
          </CardContent>
        </Card>

        <Card role="region" aria-labelledby="boundaries-heading">
          <CardHeader>
            <CardTitle id="boundaries-heading">Trust boundaries</CardTitle>
          </CardHeader>
          <CardContent>
            {analysis.trustBoundaries.length > 0 ? (
              <ul className="flex flex-col gap-3">
                {analysis.trustBoundaries.map((boundary) => (
                  <li key={boundary.id} className="flex flex-col gap-1.5">
                    <span className="text-xs font-semibold text-fg">{boundary.name}</span>
                    <ul className="flex flex-wrap gap-1.5" aria-label={`Components in ${boundary.name}`}>
                      {boundary.nodeIds.map((id) => (
                        <li
                          key={id}
                          className="rounded-sm border border-default bg-surface-2 px-2 py-0.5 text-xs text-fg-secondary"
                        >
                          {nodeName(id)}
                        </li>
                      ))}
                    </ul>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-sm text-muted">No trust boundaries were identified.</p>
            )}
          </CardContent>
        </Card>
      </div>

      <Card role="region" aria-labelledby="exposure-heading">
        <CardHeader>
          <CardTitle id="exposure-heading">Exposure</CardTitle>
        </CardHeader>
        <CardContent className="p-0">
          <Table aria-label="Component exposure">
            <thead>
              <tr>
                <Th>Component</Th>
                <Th>Exposure</Th>
                <Th>Reason</Th>
              </tr>
            </thead>
            <tbody>
              {analysis.exposure.map((row) => {
                const meta = EXPOSURE_META[row.level];
                return (
                  <tr key={row.nodeId}>
                    <Td className="whitespace-nowrap">{nodeName(row.nodeId)}</Td>
                    <Td>
                      <Badge tone={meta.tone}>
                        <meta.Icon aria-hidden />
                        {meta.label}
                      </Badge>
                    </Td>
                    <Td className="min-w-56 text-fg-secondary">{row.reason}</Td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </CardContent>
      </Card>

      <Card role="region" aria-labelledby="controls-heading">
        <CardHeader>
          <CardTitle id="controls-heading">Controls</CardTitle>
          <ProvenanceTag kind="calculated" />
        </CardHeader>
        <CardContent className="p-0">
          <ControlsMatrix controls={analysis.controls} nodeName={nodeName} />
        </CardContent>
      </Card>

      <Card role="region" aria-labelledby="threats-heading">
        <CardHeader>
          <CardTitle id="threats-heading">Threats</CardTitle>
          <ProvenanceTag kind="finding" />
        </CardHeader>
        <CardContent className="p-0">
          {analysis.threats.length > 0 ? (
            <ul className="divide-y divide-default" aria-label="Threats">
              {analysis.threats.map((threat) => (
                <li key={threat.id} className="flex flex-col gap-2 px-4 py-3">
                  <div className="flex flex-wrap items-center gap-2">
                    <SeverityBadge severity={threat.severity} />
                    <Badge tone="neutral">{STRIDE_LABEL[threat.category]}</Badge>
                  </div>
                  <p className="text-sm font-semibold text-fg">{threat.title}</p>
                  <p className="text-xs text-muted">
                    <span className="font-medium text-fg-secondary">Components:</span>{" "}
                    {threat.nodeIds.map(nodeName).join(", ")}
                  </p>
                  <p className="text-sm text-fg-secondary">
                    <span className="font-medium text-fg">Mitigation:</span> {threat.mitigation}
                  </p>
                  <div className="flex flex-wrap items-center gap-1">
                    <WhyButton evidenceId={threat.evidenceId} subject={threat.title} />
                    {threat.nodeIds.length > 0 ? (
                      <LocateLink
                        projectId={projectId}
                        mode="security"
                        nodeIds={threat.nodeIds}
                        subject={threat.title}
                      />
                    ) : null}
                  </div>
                </li>
              ))}
            </ul>
          ) : (
            <p className="flex items-start gap-2 px-4 py-3 text-sm text-fg-secondary">
              <CircleCheck aria-hidden className="mt-0.5 size-4 shrink-0 text-accent-fg" />
              No threats found.
            </p>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

export function ControlsMatrix({
  controls,
  nodeName,
}: {
  controls: readonly SecurityControls[];
  nodeName: (id: string) => string;
}) {
  if (controls.length === 0) {
    return <p className="px-4 py-3 text-sm text-muted">No component controls were reported.</p>;
  }
  return (
    <Table aria-label="Security controls by component">
      <thead>
        <tr>
          <Th>Component</Th>
          {CONTROL_COLUMNS.map((column) => (
            <Th key={column.key} title={column.long}>
              <abbr title={column.long} className="no-underline">
                {column.label}
              </abbr>
            </Th>
          ))}
          <Th title="Handles personally identifiable information">PII</Th>
        </tr>
      </thead>
      <tbody>
        {controls.map((row) => {
          const name = nodeName(row.nodeId);
          return (
            <tr key={row.nodeId}>
              <th scope="row" className="border-b border-default px-3 py-2 text-left font-normal text-fg">
                {name}
              </th>
              {CONTROL_COLUMNS.map((column) => (
                <Td key={column.key} className="whitespace-nowrap">
                  <TriStateCell value={row[column.key]} label={`${name} ${column.long}`} />
                </Td>
              ))}
              <Td className="whitespace-nowrap">
                {row.handlesPii ? (
                  <span className="inline-flex items-center gap-1 text-xs text-warning-fg">
                    <UserRound aria-hidden className="size-3.5" />
                    <span className="sr-only">{`${name}: `}</span>
                    Handles PII
                  </span>
                ) : (
                  <span className="text-xs text-muted">
                    <span className="sr-only">{`${name}: `}</span>
                    No PII
                  </span>
                )}
              </Td>
            </tr>
          );
        })}
      </tbody>
    </Table>
  );
}
