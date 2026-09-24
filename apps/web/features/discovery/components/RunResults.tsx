"use client";

import { useMemo } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import type { Architecture } from "@/types/architecture";
import type { DiscoveryRun } from "@/types/discovery";

import { connectorLabel } from "../meta";
import { ChangeSummary, componentChanges, ProposedArchitecturePreview } from "./ProposedArchitecture";
import { type ResourceStatusFilter, ResourceTable } from "./ResourceTable";
import { type SaveControls, SavePanel } from "./SavePanel";

/** DISCOVER / NORMALIZE / REVIEW / SAVE steps for one discovery run (spec §44). */
export function RunResults({
  projectId,
  run,
  current,
  pollError,
  onRetryPoll,
  onRetryScan,
  statusFilter,
  onStatusFilterChange,
  save,
}: {
  projectId: string;
  run: DiscoveryRun;
  current: Architecture | null;
  pollError: Error | null;
  onRetryPoll: () => void;
  onRetryScan: () => void;
  statusFilter: ResourceStatusFilter;
  onStatusFilterChange: (status: ResourceStatusFilter) => void;
  save: SaveControls;
}) {
  const inProgress = run.status === "queued" || run.status === "running";
  const proposed = run.proposedArchitecture;
  const changes = useMemo(
    () => (proposed && current && save.savedVersion === null ? componentChanges(proposed, current) : null),
    [proposed, current, save.savedVersion],
  );
  const addedIds = useMemo(() => new Set(changes?.added.map((n) => n.id) ?? []), [changes]);

  return (
    <div className="flex flex-col gap-6">
      {run.status === "failed" ? (
        <ErrorState
          title="Discovery failed."
          message={run.error?.message ?? "The discovery run did not complete."}
          noChangesApplied
          onRetry={onRetryScan}
          details={`Run ID: ${run.id}${run.error ? `\nCode: ${run.error.code}` : ""}`}
        />
      ) : null}

      {pollError && inProgress ? (
        <ErrorState
          title="Lost track of discovery progress."
          message={`${getErrorInfo(pollError).message} The scan may still be running on the server.`}
          requestId={getErrorInfo(pollError).requestId}
          onRetry={onRetryPoll}
        />
      ) : null}

      <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Discovery summary">
        <SummaryTile label="Resources" value={run.summary.total} live={inProgress} />
        <SummaryTile label="Mapped" value={run.summary.mapped} tone="accent" />
        <SummaryTile label="Unmapped" value={run.summary.unmapped} tone="warning" />
        <SummaryTile label="Ignored" value={run.summary.ignored} />
      </dl>

      {proposed ? (
        <Card role="region" aria-labelledby="proposal-heading">
          <CardHeader>
            <div className="flex items-center gap-2">
              <CardTitle id="proposal-heading">Proposed architecture</CardTitle>
              <span className="tabular text-xs text-muted">
                {proposed.nodes.length} components · {proposed.edges.length} connections
              </span>
            </div>
            {save.savedVersion !== null ? (
              <Badge tone="accent">Saved as v{save.savedVersion}</Badge>
            ) : (
              <ProvenanceTag kind="ai" label="Proposal · not saved" />
            )}
          </CardHeader>
          <CardContent className="grid grid-cols-1 gap-6 xl:grid-cols-[minmax(0,1fr)_320px]">
            <ProposedArchitecturePreview proposed={proposed} addedIds={addedIds} />
            <div className="flex flex-col gap-4">
              {changes && current ? (
                <ChangeSummary changes={changes} currentVersion={current.version} />
              ) : save.savedVersion === null && save.architectureReady ? (
                <p className="text-sm text-fg-secondary">
                  This project has no architecture yet. Saving makes this proposal v1.
                </p>
              ) : null}
              {run.summary.unmapped > 0 && save.savedVersion === null ? (
                <Alert
                  tone="warning"
                  title={`${run.summary.unmapped} resources are unmapped`}
                  actions={
                    <Button size="sm" onClick={() => onStatusFilterChange("unmapped")}>
                      Show unmapped
                    </Button>
                  }
                >
                  They are not part of the proposal. Review them before saving; add them on the canvas
                  afterwards if they matter.
                </Alert>
              ) : null}
              <SavePanel projectId={projectId} controls={save} proposedVersion={proposed.version} />
            </div>
          </CardContent>
        </Card>
      ) : inProgress ? (
        <div className="rounded-md border border-dashed border-default px-4 py-3 text-sm text-fg-secondary">
          The proposed architecture appears here after{" "}
          <span className="font-medium text-fg">Generate architecture</span> completes. Nothing is saved until
          you approve it.
        </div>
      ) : null}

      <Card role="region" aria-labelledby="resources-heading">
        <CardHeader>
          <div className="flex items-center gap-2">
            <CardTitle id="resources-heading">Discovered resources</CardTitle>
            <span className="tabular text-xs text-fg-secondary">
              {run.summary.total.toLocaleString("en-US")} resources
            </span>
          </div>
          <ProvenanceTag kind="fact" label={connectorLabel(run.connector)} />
        </CardHeader>
        <ResourceTable
          resources={run.resources}
          summary={run.summary}
          scanning={inProgress}
          status={statusFilter}
          onStatusChange={onStatusFilterChange}
        />
      </Card>
    </div>
  );
}

function SummaryTile({
  label,
  value,
  tone,
  live = false,
}: {
  label: string;
  value: number;
  tone?: "accent" | "warning";
  live?: boolean;
}) {
  return (
    <div className="flex flex-col gap-1 rounded-md border border-default bg-surface px-4 py-3">
      <dt className="label-caps flex items-center gap-1.5">
        {label}
        {live ? (
          <span className="inline-flex items-center gap-1 font-normal tracking-normal normal-case text-info-fg">
            <span aria-hidden className="size-1.5 rounded-full bg-info motion-safe:animate-pulse" />
            scanning
          </span>
        ) : null}
      </dt>
      <dd
        className={
          tone === "accent" && value > 0
            ? "tabular text-2xl font-semibold text-accent-fg"
            : tone === "warning" && value > 0
              ? "tabular text-2xl font-semibold text-warning-fg"
              : "tabular text-2xl font-semibold text-fg"
        }
      >
        {value.toLocaleString("en-US")}
      </dd>
    </div>
  );
}
