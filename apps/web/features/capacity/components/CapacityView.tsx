"use client";

import { Gauge, Play, RotateCw } from "lucide-react";
import Link from "next/link";
import { useCallback, useMemo } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useCapacity, useRunCapacityAnalysis } from "@/hooks/use-capacity";
import { usePageAction, useRegisterCommands } from "@/hooks/use-command";
import { useEvolution } from "@/hooks/use-evolution";
import { formatDateTime } from "@/lib/formatting";
import { nodeName as graphNodeName } from "@/lib/graph";

import { BottleneckCard } from "./BottleneckCard";
import { CurrentLoad } from "./CurrentLoad";
import { OperatingEnvelopeChart } from "./OperatingEnvelopeChart";
import { UtilizationList } from "./UtilizationList";

/** Capacity page container (spec §35–36). Displays backend capacity results only (spec §111). */
export function CapacityView({ projectId }: { projectId: string }) {
  const capacity = useCapacity(projectId);
  const architecture = useArchitecture(projectId);
  // Optional: adds the version dimension to the envelope when a roadmap exists (spec §36).
  const evolution = useEvolution(projectId);
  const { mutate: runMutation, isPending: running } = useRunCapacityAnalysis(projectId);

  const arch = architecture.data ?? null;
  const analysis = capacity.data ?? null;

  const runAnalysis = useCallback(() => {
    runMutation(undefined, {
      onSuccess: (result) =>
        toast("Capacity analysis complete", {
          tone: "success",
          description: `Calculated for architecture v${result.architectureVersion}.`,
        }),
      onError: (error) => toastError("Capacity analysis failed. No changes were applied.", error),
    });
  }, [runMutation]);

  const commands = useMemo(
    () => [
      {
        id: "analysis.capacity.run",
        label: "Analyze capacity",
        group: "Analysis" as const,
        keywords: ["capacity", "utilization", "bottleneck", "envelope", "load"],
        disabled: running || !arch,
        disabledReason: running ? "Analysis is running" : "Generate an architecture first",
        run: runAnalysis,
      },
    ],
    [running, arch, runAnalysis],
  );
  useRegisterCommands(commands);

  // "Analyze capacity" from the palette on any project page lands here with ?action=analyze.
  usePageAction(
    "analyze",
    () => {
      if (arch) runAnalysis();
      else toast("Capacity not analyzed", { description: "Generate an architecture first." });
    },
    !architecture.isPending && !capacity.isPending && !running,
  );

  const nodeName = useCallback((id: string) => (arch ? graphNodeName(arch, id) : id), [arch]);

  const runButton = (
    <Button
      variant={analysis ? "secondary" : "primary"}
      onClick={runAnalysis}
      loading={running}
      disabled={!arch}
      className="print:hidden"
    >
      {running ? null : analysis ? (
        <RotateCw aria-hidden className="size-4" />
      ) : (
        <Play aria-hidden className="size-4" />
      )}
      {running ? "Analyzing…" : analysis ? "Re-run analysis" : "Run capacity analysis"}
    </Button>
  );

  let body: React.ReactNode;
  if (capacity.isPending || architecture.isPending) {
    body = <CapacitySkeleton />;
  } else if (capacity.isError) {
    const info = getErrorInfo(capacity.error);
    body = (
      <ErrorState
        title="Capacity results could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void capacity.refetch()}
      />
    );
  } else if (architecture.isError && !analysis) {
    const info = getErrorInfo(architecture.error);
    body = (
      <ErrorState
        title="The architecture could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void architecture.refetch()}
      />
    );
  } else if (!arch && !analysis) {
    body = (
      <EmptyState
        icon={Gauge}
        title="No architecture to analyze yet."
        description="Describe your system and generate an architecture. Capacity is calculated from its components and your requirements."
        action={
          <Button asChild variant="primary">
            <Link href={`/project/${encodeURIComponent(projectId)}/requirements`}>Describe system</Link>
          </Button>
        }
      />
    );
  } else if (!analysis) {
    body = (
      <EmptyState
        icon={Gauge}
        title={`Capacity not analyzed yet${arch ? ` for v${arch.version}` : ""}.`}
        description="Run capacity analysis to calculate current load, per-component utilization, the next bottleneck and the operating envelope."
        action={runButton}
      />
    );
  } else {
    const stale = arch !== null && arch.version !== analysis.architectureVersion;
    body = (
      <>
        {stale ? (
          <Alert
            tone="warning"
            title={`These results are for v${analysis.architectureVersion}; the architecture is now v${arch.version}.`}
            actions={
              <Button size="sm" onClick={runAnalysis} loading={running} className="print:hidden">
                Re-run for v{arch.version}
              </Button>
            }
          >
            Numbers below may not reflect the latest changes.
          </Alert>
        ) : null}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="flex min-w-0 flex-col gap-6">
            <CurrentLoad load={analysis.load} />

            <Card role="region" aria-labelledby="utilization-heading">
              <CardHeader>
                <CardTitle id="utilization-heading">System utilization</CardTitle>
                <ProvenanceTag kind="calculated" />
              </CardHeader>
              <CardContent className="p-0">
                <UtilizationList utilization={analysis.utilization} nodeName={nodeName} />
              </CardContent>
            </Card>
          </div>

          <BottleneckCard projectId={projectId} bottleneck={analysis.bottleneck} nodeName={nodeName} />
        </div>

        <Card role="region" aria-labelledby="envelope-heading">
          <CardHeader>
            <CardTitle id="envelope-heading">Operating envelope</CardTitle>
            <ProvenanceTag kind="calculated" />
          </CardHeader>
          <CardContent>
            <ErrorBoundary label="Operating envelope">
              <OperatingEnvelopeChart
                points={analysis.envelope.points}
                maxSupportedDailyActiveUsers={analysis.envelope.maxSupportedDailyActiveUsers}
                stages={evolution.data?.stages}
              />
            </ErrorBoundary>
          </CardContent>
        </Card>
      </>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Capacity"
        description="Load, utilization and headroom calculated by the capacity engine."
        meta={
          analysis ? (
            <>
              <ProvenanceTag kind="calculated" label="Capacity engine" />
              <span>
                Architecture <span className="tabular">v{analysis.architectureVersion}</span>
              </span>
              <span aria-hidden>·</span>
              <span>
                Calculated{" "}
                <time dateTime={analysis.calculatedAt}>{formatDateTime(analysis.calculatedAt)}</time>
              </span>
            </>
          ) : undefined
        }
        actions={analysis ? runButton : undefined}
      />
      {body}
    </div>
  );
}

function CapacitySkeleton() {
  return (
    <SkeletonGroup label="Loading capacity analysis" className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
        <Skeleton className="h-72" />
        <Skeleton className="h-48" />
      </div>
      <Skeleton className="h-48" />
    </SkeletonGroup>
  );
}
