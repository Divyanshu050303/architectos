"use client";

import { FlaskConical, Map as MapIcon } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorBoundary } from "@/components/feedback/ErrorBoundary";
import { ErrorState } from "@/components/feedback/ErrorState";
import { LoadingSteps } from "@/components/feedback/LoadingSteps";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { projectHref } from "@/config/navigation";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import {
  useLatestSimulation,
  useRunSimulation,
  useSimulationRun,
  useSimulationScenarios,
} from "@/hooks/use-simulation";
import { nodeName as graphNodeName } from "@/lib/graph";
import type { SimulationConfig, SimulationRun, SimulationScenario } from "@/types/simulation";

import { PropagationTimeline } from "./PropagationTimeline";
import { SimulationForm } from "./SimulationForm";
import { SimulationResultCard } from "./SimulationResultCard";

type FormSettings = Omit<SimulationConfig, "scenarioId">;

const DEFAULT_SETTINGS: FormSettings = {
  traffic: "current",
  durationMinutes: 5,
  environment: "production_like",
};

function isActive(run: SimulationRun | null | undefined): boolean {
  return run?.status === "queued" || run?.status === "running";
}

/**
 * Scenario preselection: `?scenario=<id>` wins, then `?node=<id>` from the workspace's
 * "Simulate failure" (a scenario targeting only that node first), then the latest run's scenario.
 */
function pickScenario(
  scenarios: readonly SimulationScenario[],
  scenarioParam: string | null,
  nodeParam: string | null,
  latest: SimulationRun | null,
): string {
  const byParam = scenarios.find((s) => s.id === scenarioParam);
  if (byParam) return byParam.id;
  if (nodeParam) {
    const exact = scenarios.find((s) => s.targetNodeIds.length === 1 && s.targetNodeIds[0] === nodeParam);
    const any = scenarios.find((s) => s.targetNodeIds.includes(nodeParam));
    const match = exact ?? any;
    if (match) return match.id;
  }
  const fromLatest = scenarios.find((s) => s.id === latest?.scenarioId);
  return fromLatest?.id ?? scenarios[0]?.id ?? "";
}

/**
 * Simulation page (spec §40–41). Runs are backend jobs and are never optimistic (spec §50):
 * the page shows the engine's steps while it runs and its result once it finishes.
 */
export function SimulationView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();

  const scenarios = useSimulationScenarios(projectId);
  const latest = useLatestSimulation(projectId);
  const architecture = useArchitecture(projectId);
  const { mutate: startRun, isPending: starting } = useRunSimulation(projectId);

  const [settings, setSettings] = useState<FormSettings>(DEFAULT_SETTINGS);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const activeRun = useSimulationRun(activeRunId);

  const arch = architecture.data ?? null;
  const scenarioList = useMemo(() => scenarios.data ?? [], [scenarios.data]);
  const run = (activeRunId ? activeRun.data : undefined) ?? latest.data ?? null;
  const running = starting || isActive(run);

  const scenarioId = pickScenario(
    scenarioList,
    searchParams.get("scenario"),
    searchParams.get("node"),
    latest.data ?? null,
  );
  const config: SimulationConfig = { scenarioId, ...settings };

  const nodeName = useCallback((id: string) => (arch ? graphNodeName(arch, id) : id), [arch]);
  const scenarioLabel = (id: string) => scenarioList.find((s) => s.id === id)?.label ?? id;

  const onChange = (patch: Partial<SimulationConfig>) => {
    const { scenarioId: nextScenario, ...rest } = patch;
    if (Object.keys(rest).length > 0) setSettings((prev) => ({ ...prev, ...rest }));
    if (nextScenario !== undefined) {
      const params = new URLSearchParams(searchParams.toString());
      params.set("scenario", nextScenario);
      params.delete("node");
      router.replace(`${pathname}?${params.toString()}`, { scroll: false });
    }
  };

  const runSimulation = useCallback(
    (runConfig: SimulationConfig) => {
      startRun(runConfig, {
        onSuccess: (started) => setActiveRunId(started.id),
        onError: (error) => toastError("The simulation could not be started. Nothing was run.", error),
      });
    },
    [startRun],
  );

  const canRun = scenarioId !== "" && !running;
  // A fresh array each render is fine: the registry reads `run` through a ref (hooks/use-command).
  const commands = [
    {
      id: "analysis.simulation.run",
      label: "Run simulation",
      group: "Analysis" as const,
      keywords: ["simulate", "failure", "scenario", "chaos", "outage"],
      disabled: !canRun,
      disabledReason: running ? "A simulation is running" : "No scenario to run",
      run: () => runSimulation(config),
    },
  ];
  useRegisterCommands(commands);

  const canvasHref = (runId: string) =>
    `${projectHref(projectId, "architecture")}?mode=simulation&simulation=${encodeURIComponent(runId)}`;

  let content: React.ReactNode;
  if (scenarios.isPending || latest.isPending || architecture.isPending) {
    content = <SimulationSkeleton />;
  } else if (scenarios.isError || latest.isError) {
    const failed = scenarios.isError ? scenarios : latest;
    const info = getErrorInfo(failed.error);
    content = (
      <ErrorState
        title={
          scenarios.isError
            ? "Simulation scenarios could not be loaded."
            : "The latest simulation could not be loaded."
        }
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void failed.refetch()}
      />
    );
  } else if (!arch && scenarioList.length === 0) {
    content = (
      <EmptyState
        icon={FlaskConical}
        title="No architecture to simulate yet."
        description="Describe your system and generate an architecture. Failure and traffic scenarios are derived from its databases, caches, queues and entry points."
        action={
          <Button asChild variant="primary">
            <Link href={projectHref(projectId, "requirements")}>Describe system</Link>
          </Button>
        }
      />
    );
  } else if (scenarioList.length === 0) {
    content = (
      <EmptyState
        icon={FlaskConical}
        title="No scenarios for this architecture."
        description="Scenarios come from databases, caches, queues and entry points. Add one of these to the architecture, then come back to simulate its failure."
        action={
          <Button asChild variant="primary">
            <Link href={projectHref(projectId, "architecture")}>Open architecture</Link>
          </Button>
        }
      />
    );
  } else {
    content = (
      <div className="grid grid-cols-1 items-start gap-6 lg:grid-cols-[300px_minmax(0,1fr)]">
        <Card className="lg:sticky lg:top-4">
          <CardHeader>
            <CardTitle>Simulation</CardTitle>
            <Badge tone="neutral">
              <FlaskConical aria-hidden />
              Experimental
            </Badge>
          </CardHeader>
          <CardContent>
            <ErrorBoundary label="Simulation settings">
              <SimulationForm
                scenarios={scenarioList}
                value={config}
                onChange={onChange}
                onRun={() => runSimulation(config)}
                running={running}
              />
            </ErrorBoundary>
          </CardContent>
        </Card>

        <div className="flex min-w-0 flex-col gap-6">
          {/* Keyed by run so a new run gets a fresh boundary (spec §82). */}
          <ErrorBoundary key={run?.id ?? "none"} label="Simulation results">
            <RunResults
              run={run}
              archVersion={arch?.version ?? null}
              scenarioLabel={scenarioLabel}
              nodeName={nodeName}
              canvasHref={canvasHref}
              onRetry={run ? () => runSimulation(run.config) : undefined}
            />
          </ErrorBoundary>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Simulation"
        description="Inject failures and traffic against the architecture and see how the impact propagates — before production does."
        meta={
          <>
            <ProvenanceTag kind="calculated" label="Simulation engine" />
            {arch ? (
              <span>
                Architecture <span className="tabular">v{arch.version}</span>
              </span>
            ) : null}
          </>
        }
      />
      {content}
    </div>
  );
}

interface RunResultsProps {
  run: SimulationRun | null;
  archVersion: number | null;
  scenarioLabel: (id: string) => string;
  nodeName: (id: string) => string;
  canvasHref: (runId: string) => string;
  onRetry?: () => void;
}

function RunResults({ run, archVersion, scenarioLabel, nodeName, canvasHref, onRetry }: RunResultsProps) {
  if (!run) {
    return (
      <EmptyState
        icon={FlaskConical}
        title="No simulation run yet."
        description="Pick a scenario, traffic level, duration and environment, then run it to see the impact, the affected components and how the failure propagates."
      />
    );
  }

  if (isActive(run)) {
    return (
      <Card>
        <CardHeader>
          <CardTitle>Running · {scenarioLabel(run.scenarioId)}</CardTitle>
          <ProvenanceTag kind="calculated" label="Simulation engine" />
        </CardHeader>
        <CardContent>
          <LoadingSteps title="Simulating" steps={run.steps} />
        </CardContent>
      </Card>
    );
  }

  if (run.status === "failed" || !run.result) {
    return (
      <ErrorState
        title={`Simulation failed: ${scenarioLabel(run.scenarioId)}.`}
        message={run.error?.message ?? "The simulation engine returned no result."}
        details={run.error ? `${run.error.code}: ${run.error.message}` : undefined}
        noChangesApplied
        onRetry={onRetry}
      />
    );
  }

  const result = run.result;
  const stale = archVersion !== null && archVersion !== run.architectureVersion;
  const viewOnCanvas = (
    <Button asChild variant="secondary" size="sm">
      <Link href={canvasHref(run.id)}>
        <MapIcon aria-hidden className="size-3.5" />
        View on canvas
      </Link>
    </Button>
  );

  return (
    <>
      {stale ? (
        <Alert
          tone="warning"
          title={`This run simulated v${run.architectureVersion}; the architecture is now v${archVersion}.`}
        >
          Run the scenario again to simulate the latest changes.
        </Alert>
      ) : null}
      <ErrorBoundary label="Simulation result">
        <SimulationResultCard
          run={run}
          result={result}
          scenarioLabel={scenarioLabel(run.scenarioId)}
          nodeName={nodeName}
        />
      </ErrorBoundary>
      <Card role="region" aria-labelledby="simulation-propagation-heading">
        <CardHeader>
          <CardTitle id="simulation-propagation-heading">Propagation</CardTitle>
          {viewOnCanvas}
        </CardHeader>
        <CardContent>
          <ErrorBoundary label="Propagation timeline">
            <PropagationTimeline timeline={result.timeline} nodeName={nodeName} />
          </ErrorBoundary>
        </CardContent>
      </Card>
    </>
  );
}

function SimulationSkeleton() {
  return (
    <SkeletonGroup
      label="Loading simulation"
      className="grid grid-cols-1 gap-6 lg:grid-cols-[300px_minmax(0,1fr)]"
    >
      <Skeleton className="h-80" />
      <div className="flex flex-col gap-6">
        <Skeleton className="h-64" />
        <Skeleton className="h-72" />
      </div>
    </SkeletonGroup>
  );
}
