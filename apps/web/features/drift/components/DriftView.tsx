"use client";

import { Boxes, CheckCircle2, Play, RotateCw, Server } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

import { getErrorInfo, isApiError } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Card, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { projectHref } from "@/config/navigation";
import { connectorLabel } from "@/features/discovery/meta";
import { toastError } from "@/features/validation/notify";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useCheckDrift, useDrift } from "@/hooks/use-drift";
import { formatDateTime } from "@/lib/formatting";

import { isDriftFilter } from "../meta";
import { DriftTable } from "./DriftTable";

/**
 * Architecture drift (spec §45): the saved architecture (EXPECTED) next to what the
 * backend drift checker found deployed (ACTUAL). The filter is deep-linkable via
 * `?show=all` (spec §52); the default shows only differences.
 */
export function DriftView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const drift = useDrift(projectId);
  const architecture = useArchitecture(projectId);
  const check = useCheckDrift(projectId);

  const show = searchParams.get("show");
  const filter = isDriftFilter(show) ? show : "drifted";
  const report = drift.data ?? null;
  const arch = architecture.data ?? null;
  const infrastructureHref = projectHref(projectId, "infrastructure");

  const setFilter = useCallback(
    (next: "drifted" | "all") => {
      const params = new URLSearchParams(searchParams.toString());
      if (next === "drifted") params.delete("show");
      else params.set("show", next);
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const runCheck = useCallback(() => {
    check.mutate(undefined, {
      onSuccess: (result) =>
        toast("Drift check complete", {
          tone: "success",
          description: `${result.summary.drifted} drifted · ${result.summary.matching} matching for v${result.architectureVersion}.`,
        }),
      onError: (error) => {
        // discovery_required is explained inline with a link to connect a source.
        if (!isApiError(error, "discovery_required"))
          toastError("Drift check failed. No changes were applied.", error);
      },
    });
  }, [check]);

  useRegisterCommands([
    {
      id: "analysis.drift.check",
      label: "Run drift check",
      group: "Analysis",
      keywords: ["drift", "expected", "actual", "deployed", "infrastructure"],
      disabled: check.isPending || !arch,
      run: runCheck,
    },
  ]);

  const runButton = (
    <Button
      variant={report ? "secondary" : "primary"}
      onClick={runCheck}
      loading={check.isPending}
      disabled={!arch}
      className="print:hidden"
    >
      {check.isPending ? null : report ? (
        <RotateCw aria-hidden className="size-4" />
      ) : (
        <Play aria-hidden className="size-4" />
      )}
      {check.isPending ? "Checking…" : report ? "Re-run drift check" : "Run drift check"}
    </Button>
  );

  const checkError =
    check.isError && isApiError(check.error, "discovery_required") ? getErrorInfo(check.error) : null;
  const checkErrorView = checkError ? (
    <Alert
      tone="danger"
      title="Drift check needs a discovery source."
      actions={
        <Button asChild size="sm" variant="primary">
          <Link href={infrastructureHref}>
            <Server aria-hidden className="size-3.5" />
            Connect infrastructure
          </Link>
        </Button>
      }
    >
      <p>
        {checkError.message} Scan AWS, Kubernetes or Terraform under Infrastructure, then run the check again.
        No changes were applied.
      </p>
      <p className="mt-1 text-xs text-muted">
        Request ID: <span className="tabular text-fg-secondary">{checkError.requestId}</span>
      </p>
    </Alert>
  ) : null;

  let body: React.ReactNode;
  if (drift.isPending || architecture.isPending) {
    body = (
      <SkeletonGroup label="Loading drift report" className="flex flex-col gap-6">
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
          {[0, 1, 2, 3].map((i) => (
            <Skeleton key={i} className="h-20" />
          ))}
        </div>
        <Skeleton className="h-72" />
      </SkeletonGroup>
    );
  } else if (drift.isError) {
    const info = getErrorInfo(drift.error);
    body = (
      <ErrorState
        title="The drift report could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void drift.refetch()}
      />
    );
  } else if (!report && !arch) {
    body = (
      <EmptyState
        icon={Boxes}
        title="No architecture to compare yet."
        description="Drift compares a saved architecture with what is deployed. Discover your infrastructure to import one, or describe the system to generate one."
        action={
          <>
            <Button asChild variant="primary">
              <Link href={infrastructureHref}>Discover infrastructure</Link>
            </Button>
            <Button asChild>
              <Link href={projectHref(projectId, "requirements")}>Describe system</Link>
            </Button>
          </>
        }
      />
    );
  } else if (!report) {
    body = (
      <>
        {checkErrorView}
        <EmptyState
          icon={Boxes}
          title={`Drift not checked yet${arch ? ` for v${arch.version}` : ""}.`}
          description="Run a drift check to compare the saved architecture (expected) with what is deployed (actual). It uses your connected discovery source: AWS, Kubernetes or Terraform."
          action={
            <>
              {runButton}
              <Button asChild variant="ghost">
                <Link href={infrastructureHref}>Connect a source</Link>
              </Button>
            </>
          }
        />
      </>
    );
  } else {
    const stale = arch !== null && arch.version !== report.architectureVersion;
    const items = filter === "all" ? report.items : report.items.filter((i) => i.status !== "matching");
    body = (
      <>
        {checkErrorView}
        {stale ? (
          <Alert
            tone="warning"
            title={`This check compared v${report.architectureVersion}; the architecture is now v${arch.version}.`}
            actions={
              <Button size="sm" onClick={runCheck} loading={check.isPending} className="print:hidden">
                Re-check v{arch.version}
              </Button>
            }
          >
            Expected values below may not reflect the latest version.
          </Alert>
        ) : null}

        <dl className="grid grid-cols-2 gap-3 sm:grid-cols-4" aria-label="Drift summary">
          <Tile
            label="Drifted"
            value={report.summary.drifted}
            tone={report.summary.drifted > 0 ? "warning" : "fg"}
          />
          <Tile label="Matching" value={report.summary.matching} tone="accent" />
          <Tile label="Source" text={connectorLabel(report.source)} />
          <Tile label="Architecture" text={`v${report.architectureVersion}`} mono />
        </dl>

        {report.items.length === 0 ? (
          <EmptyState
            icon={CheckCircle2}
            title="No drift detected."
            description={`Everything ${connectorLabel(report.source)} reported matches architecture v${report.architectureVersion}. Re-run the check after deployments.`}
          />
        ) : (
          <Card role="region" aria-labelledby="drift-heading">
            <CardHeader className="flex-wrap">
              <div className="flex items-center gap-2">
                <CardTitle id="drift-heading">Expected vs actual</CardTitle>
                <ProvenanceTag kind="calculated" label="Drift checker" />
              </div>
              <div role="group" aria-label="Show" className="flex gap-1">
                {(
                  [
                    ["drifted", `Drifted ${report.summary.drifted}`],
                    ["all", `All ${report.items.length}`],
                  ] as const
                ).map(([value, label]) => (
                  <Button
                    key={value}
                    size="sm"
                    variant={filter === value ? "primary" : "secondary"}
                    aria-pressed={filter === value}
                    onClick={() => setFilter(value)}
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </CardHeader>
            <DriftTable
              items={items}
              expectedLabel={`Architecture v${report.architectureVersion}`}
              actualLabel={connectorLabel(report.source)}
              locateHref={(nodeId) =>
                `${projectHref(projectId, "architecture")}?highlight=${encodeURIComponent(nodeId)}&node=${encodeURIComponent(nodeId)}`
              }
              emptyMessage={
                filter === "drifted" ? (
                  <>
                    No differences: everything matches.{" "}
                    <button
                      type="button"
                      className="font-medium text-accent-fg underline"
                      onClick={() => setFilter("all")}
                    >
                      Show all {report.items.length}
                    </button>
                  </>
                ) : (
                  "No items."
                )
              }
            />
          </Card>
        )}
      </>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Drift"
        description="Where deployed infrastructure no longer matches the saved architecture."
        meta={
          report ? (
            <>
              <ProvenanceTag kind="calculated" label="Drift checker" />
              <span>
                Source <span className="text-fg-secondary">{connectorLabel(report.source)}</span>
              </span>
              <span aria-hidden>·</span>
              <span>
                Checked <time dateTime={report.checkedAt}>{formatDateTime(report.checkedAt)}</time>
              </span>
            </>
          ) : undefined
        }
        actions={
          report ? (
            <>
              <Button asChild variant="ghost">
                <Link href={infrastructureHref}>
                  <Server aria-hidden className="size-4" />
                  Discovery
                </Link>
              </Button>
              {runButton}
            </>
          ) : undefined
        }
      />
      {body}
    </div>
  );
}

function Tile({
  label,
  value,
  text,
  tone = "fg",
  mono = false,
}: {
  label: string;
  value?: number;
  text?: string;
  tone?: "fg" | "warning" | "accent";
  mono?: boolean;
}) {
  const color = tone === "warning" ? "text-warning-fg" : tone === "accent" ? "text-accent-fg" : "text-fg";
  return (
    <div className="flex flex-col gap-1 rounded-md border border-default bg-surface px-4 py-3">
      <dt className="label-caps">{label}</dt>
      <dd
        className={
          value !== undefined
            ? `tabular text-2xl font-semibold ${color}`
            : `${mono ? "tabular " : ""}text-base font-semibold text-fg`
        }
      >
        {value !== undefined ? value : text}
      </dd>
    </div>
  );
}
