"use client";

import { RotateCcw, Server } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { getErrorInfo, isApiError } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useConnectors, useDiscoveryRun, useSaveDiscovery, useStartDiscovery } from "@/hooks/use-discovery";
import type { DiscoveryConnectorKind, DiscoveryOptions } from "@/types/discovery";

import { cleanOptions, connectorLabel, discoveryStages } from "../meta";
import { ConnectSection } from "./ConnectSection";
import { DiscoveryStepper } from "./DiscoveryStepper";
import type { ResourceStatusFilter } from "./ResourceTable";
import { RunResults } from "./RunResults";

/**
 * Brownfield discovery (spec §44): scan live infrastructure, review what the backend
 * mapped and proposed, and save it as a new architecture version only on explicit
 * approval (spec §89). The run is deep-linkable via `?run=<id>` (spec §52).
 */
export function DiscoveryView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const runId = searchParams.get("run");

  const connectors = useConnectors();
  const architecture = useArchitecture(projectId);
  const start = useStartDiscovery(projectId);
  const runQuery = useDiscoveryRun(runId);
  const save = useSaveDiscovery(projectId);
  const [statusFilter, setStatusFilter] = useState<ResourceStatusFilter>("all");

  const run = runQuery.data ?? null;
  const arch = architecture.data ?? null;
  const startingConnector = start.isPending ? (start.variables?.connector ?? null) : null;
  const savedVersion = save.isSuccess && save.variables.runId === runId ? save.data.version : null;
  const canSave =
    run !== null &&
    run.status === "succeeded" &&
    run.proposedArchitecture !== null &&
    savedVersion === null &&
    !save.isPending &&
    architecture.isSuccess;

  const setRun = useCallback(
    (id: string | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (id) params.set("run", id);
      else params.delete("run");
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const startScan = (connector: DiscoveryConnectorKind, options: DiscoveryOptions) => {
    if (start.isPending) return;
    save.reset();
    setStatusFilter("all");
    start.mutate(
      { connector, options: cleanOptions(options) },
      { onSuccess: (started) => setRun(started.id) },
    );
  };

  const newScan = () => {
    start.reset();
    save.reset();
    setStatusFilter("all");
    setRun(null);
  };

  const saveRun = () => {
    if (!canSave || !runId || !run) return;
    // The version the user reviewed the proposal against; the backend rejects a stale base (409).
    const baseVersion = arch?.version ?? null;
    const label = connectorLabel(run.connector);
    save.mutate(
      { runId, baseVersion },
      {
        onSuccess: (saved) =>
          toast(`Saved as v${saved.version}`, {
            tone: "success",
            description: `Imported from ${label} discovery.${baseVersion ? ` v${baseVersion} stays in version history.` : ""}`,
          }),
      },
    );
  };

  useRegisterCommands([
    {
      id: "discovery.save",
      label: "Save discovered architecture",
      group: "Architecture",
      keywords: ["discovery", "import", "brownfield", "infrastructure"],
      disabled: !canSave,
      run: saveRun,
    },
    {
      id: "discovery.new-scan",
      label: "New infrastructure scan",
      group: "Analysis",
      keywords: ["discovery", "aws", "kubernetes", "terraform", "scan"],
      disabled: runId === null,
      run: newScan,
    },
  ]);

  const stages = discoveryStages({ run, startingConnector, saving: save.isPending, savedVersion });

  let body: React.ReactNode;
  if (runId === null) {
    body = (
      <ConnectSection
        connectors={connectors}
        start={start}
        currentVersion={arch?.version ?? null}
        onStart={startScan}
      />
    );
  } else if (runQuery.isPending) {
    body = (
      <SkeletonGroup label="Loading discovery run" className="flex flex-col gap-4">
        <Skeleton className="h-20" />
        <Skeleton className="h-80" />
      </SkeletonGroup>
    );
  } else if (!run) {
    const info = getErrorInfo(runQuery.error);
    body = isApiError(runQuery.error, "discovery_not_found") ? (
      <EmptyState
        icon={Server}
        title="This discovery run no longer exists."
        description="Runs are kept for a limited time. Start a new scan to discover the current infrastructure."
        action={
          <Button variant="primary" onClick={newScan}>
            Start a new scan
          </Button>
        }
      />
    ) : (
      <ErrorState
        title="The discovery run could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void runQuery.refetch()}
      />
    );
  } else {
    body = (
      <RunResults
        projectId={projectId}
        run={run}
        current={arch}
        pollError={runQuery.isError ? runQuery.error : null}
        onRetryPoll={() => void runQuery.refetch()}
        onRetryScan={() => startScan(run.connector, {})}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        save={{
          canSave,
          saving: save.isPending,
          savedVersion,
          error: save.isError ? save.error : null,
          onSave: saveRun,
          architectureReady: architecture.isSuccess,
          architectureError: architecture.isError ? architecture.error : null,
          onRefreshArchitecture: () => {
            save.reset();
            void architecture.refetch();
            void runQuery.refetch();
          },
        }}
      />
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Infrastructure discovery"
        description="Scan live infrastructure, review how it maps to components, and save it as an architecture version."
        meta={
          run ? (
            <>
              <ProvenanceTag kind="fact" label={`Discovered from ${connectorLabel(run.connector)}`} />
              <span>
                Run <span className="tabular text-fg-secondary">{run.id}</span>
              </span>
            </>
          ) : undefined
        }
        actions={
          runId ? (
            <Button onClick={newScan} disabled={save.isPending}>
              <RotateCcw aria-hidden className="size-4" />
              New scan
            </Button>
          ) : undefined
        }
      />

      <Card>
        <CardContent className="py-3">
          <DiscoveryStepper stages={stages} />
        </CardContent>
      </Card>

      {body}
    </div>
  );
}
