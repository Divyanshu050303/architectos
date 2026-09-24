"use client";

import { ArrowRight, Save } from "lucide-react";
import Link from "next/link";

import { getErrorInfo, isApiError } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { projectHref } from "@/config/navigation";
import { isRecord } from "@/lib/utils";

/** Save state and actions the discovery container hands to the review step (spec §89). */
export interface SaveControls {
  canSave: boolean;
  saving: boolean;
  savedVersion: number | null;
  error: Error | null;
  onSave: () => void;
  architectureReady: boolean;
  architectureError: Error | null;
  onRefreshArchitecture: () => void;
}

function latestVersionOf(error: unknown): number | null {
  if (!isApiError(error) || !isRecord(error.details)) return null;
  const latest = error.details.latestVersion;
  return typeof latest === "number" ? latest : null;
}

/** Explicit approval step: saves the discovered proposal as a new architecture version. */
export function SavePanel({
  projectId,
  controls,
  proposedVersion,
}: {
  projectId: string;
  controls: SaveControls;
  proposedVersion: number;
}) {
  const architectureHref = projectHref(projectId, "architecture");

  if (controls.savedVersion !== null) {
    return (
      <Alert
        tone="success"
        title={`Saved as v${controls.savedVersion}`}
        actions={
          <Button asChild size="sm" variant="primary">
            <Link href={architectureHref}>
              Open architecture v{controls.savedVersion}
              <ArrowRight aria-hidden className="size-3.5" />
            </Link>
          </Button>
        }
      >
        The discovered architecture is now the current version.
      </Alert>
    );
  }

  const error = controls.error;
  const info = error ? getErrorInfo(error) : null;
  let errorView: React.ReactNode = null;
  if (error && info) {
    if (isApiError(error, "version_conflict")) {
      const latest = latestVersionOf(error);
      errorView = (
        <Alert
          tone="danger"
          title="Not saved: the architecture changed."
          actions={
            <Button size="sm" onClick={controls.onRefreshArchitecture}>
              Refresh comparison
            </Button>
          }
        >
          <p>
            {latest !== null ? `The current architecture is now v${latest}. ` : ""}
            Refresh the comparison, review the changes again, then save. No changes were applied.
          </p>
          <p className="mt-1 text-xs text-muted">
            Request ID: <span className="tabular text-fg-secondary">{info.requestId}</span>
          </p>
        </Alert>
      );
    } else if (isApiError(error, "discovery_already_saved")) {
      errorView = (
        <Alert
          tone="info"
          title="This discovery was already saved."
          actions={
            <Button asChild size="sm">
              <Link href={architectureHref}>Open architecture</Link>
            </Button>
          }
        >
          {info.message}
        </Alert>
      );
    } else {
      errorView = (
        <ErrorState
          title="The discovered architecture was not saved."
          message={info.message}
          requestId={info.requestId}
          noChangesApplied
          onRetry={controls.onSave}
        />
      );
    }
  }

  return (
    <div className="flex flex-col gap-3 border-t border-default pt-4">
      <p className="text-sm text-fg-secondary">
        Saving creates <span className="tabular font-medium text-fg">v{proposedVersion}</span> from this
        proposal. Nothing changes until you save.
      </p>
      {controls.architectureError ? (
        <ErrorState
          title="The current architecture could not be loaded."
          message={`${getErrorInfo(controls.architectureError).message} Saving needs it to detect conflicting edits.`}
          requestId={getErrorInfo(controls.architectureError).requestId}
          onRetry={controls.onRefreshArchitecture}
        />
      ) : null}
      {errorView}
      <Button
        variant="primary"
        onClick={controls.onSave}
        loading={controls.saving}
        disabled={!controls.canSave}
        className="self-start"
      >
        {controls.saving ? null : <Save aria-hidden className="size-4" />}
        {controls.saving ? "Saving…" : `Save as v${proposedVersion}`}
      </Button>
    </div>
  );
}
