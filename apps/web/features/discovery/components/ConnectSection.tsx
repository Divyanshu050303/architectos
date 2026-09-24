"use client";

import { getErrorInfo, isApiError } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import type { useConnectors, useStartDiscovery } from "@/hooks/use-discovery";
import type { DiscoveryConnectorKind, DiscoveryOptions } from "@/types/discovery";

import { connectorLabel } from "../meta";
import { ConnectorCard } from "./ConnectorCard";

/** CONNECT step (spec §44): pick a read-only source and start a scan. */
export function ConnectSection({
  connectors,
  start,
  currentVersion,
  onStart,
}: {
  connectors: ReturnType<typeof useConnectors>;
  start: ReturnType<typeof useStartDiscovery>;
  currentVersion: number | null;
  onStart: (connector: DiscoveryConnectorKind, options: DiscoveryOptions) => void;
}) {
  if (connectors.isPending) {
    return (
      <SkeletonGroup label="Loading discovery sources" className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-60" />
        ))}
      </SkeletonGroup>
    );
  }
  if (connectors.isError) {
    const info = getErrorInfo(connectors.error);
    return (
      <ErrorState
        title="Discovery sources could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void connectors.refetch()}
      />
    );
  }

  const failedKind = start.isError ? start.variables.connector : null;
  const startError = start.isError ? getErrorInfo(start.error) : null;
  const connectedCount = connectors.data.filter((c) => c.status === "connected").length;

  return (
    <section aria-labelledby="connect-heading" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id="connect-heading" className="label-caps">
          Connect a source
        </h2>
        <p className="text-xs text-muted">
          <span className="tabular">{connectedCount}</span> of{" "}
          <span className="tabular">{connectors.data.length}</span> sources connected · read-only access
        </p>
      </div>

      {failedKind && startError ? (
        <ErrorState
          title={`${connectorLabel(failedKind)} scan could not start.`}
          message={
            isApiError(start.error, "connector_not_connected")
              ? `${startError.message} Add read-only ${connectorLabel(failedKind)} credentials in project settings, or scan a connected source instead.`
              : startError.message
          }
          requestId={startError.requestId}
          noChangesApplied
          onRetry={
            isApiError(start.error, "connector_not_connected") ? undefined : () => onStart(failedKind, {})
          }
        />
      ) : null}

      <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
        {connectors.data.map((connector) => (
          <ConnectorCard
            key={connector.kind}
            connector={connector}
            starting={start.isPending && start.variables.connector === connector.kind}
            disabled={start.isPending}
            onStart={(options) => onStart(connector.kind, options)}
          />
        ))}
      </div>

      <p className="text-xs text-muted">
        Scanning never changes your architecture.{" "}
        {currentVersion !== null
          ? `You review the proposal first; saving creates v${currentVersion + 1} and keeps v${currentVersion} in history.`
          : "You review the proposal first; saving creates v1."}
      </p>
    </section>
  );
}
