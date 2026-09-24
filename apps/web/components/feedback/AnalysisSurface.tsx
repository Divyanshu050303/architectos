"use client";

import {
  ArrowUpRight,
  Check,
  CircleHelp,
  LocateFixed,
  type LucideIcon,
  Play,
  RotateCw,
  X,
} from "lucide-react";
import Link from "next/link";
import { useCallback } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useArchitecture } from "@/hooks/use-architecture";
import { formatDateTime } from "@/lib/formatting";
import { nodeName as graphNodeName } from "@/lib/graph";
import { cn } from "@/lib/utils";
import type { Architecture } from "@/types/architecture";

/** Overlay modes the OPERATE surfaces link into on the architecture canvas (spec §52, §68). */
export type OverlayMode = "reliability" | "security" | "observability" | "cost";

/** `/project/{id}/architecture?mode=…&highlight=a,b&node=a` — deep link that locates components (spec §52). */
export function locateHref(
  projectId: string,
  options: { mode?: OverlayMode; highlight?: readonly string[]; node?: string },
): string {
  const params = new URLSearchParams();
  if (options.mode) params.set("mode", options.mode);
  if (options.highlight && options.highlight.length > 0) params.set("highlight", options.highlight.join(","));
  if (options.node) params.set("node", options.node);
  const query = params.toString();
  return `/project/${encodeURIComponent(projectId)}/architecture${query ? `?${query}` : ""}`;
}

/** "Locate" link to the components on the canvas. */
export function LocateLink({
  projectId,
  mode,
  nodeIds,
  subject,
}: {
  projectId: string;
  mode: OverlayMode;
  nodeIds: readonly string[];
  /** Included in the accessible name, e.g. "PostgreSQL". */
  subject: string;
}) {
  const [first] = nodeIds;
  return (
    <Button asChild variant="ghost" size="sm" className="h-6 px-1.5">
      <Link
        href={locateHref(projectId, { mode, highlight: nodeIds, node: first })}
        aria-label={`Locate ${subject}`}
      >
        <LocateFixed aria-hidden className="size-3.5" />
        Locate
      </Link>
    </Button>
  );
}

/**
 * A yes / no / unknown cell (security controls, telemetry coverage). Always icon + text
 * so the value never depends on colour alone (spec §62). `null` is "Unknown".
 */
export function TriStateCell({
  value,
  label,
  yes = "Yes",
  no = "No",
}: {
  value: boolean | null;
  /** What the cell describes, e.g. "API authentication"; read by screen readers. */
  label: string;
  yes?: string;
  no?: string;
}) {
  if (value === null) {
    return (
      <span className="inline-flex items-center gap-1 text-xs text-muted" title={`${label}: unknown`}>
        <CircleHelp aria-hidden className="size-3.5" />
        <span>Unknown</span>
        <span className="sr-only">{`: ${label} is not specified in the architecture`}</span>
      </span>
    );
  }
  const Icon = value ? Check : X;
  return (
    <span
      className={cn("inline-flex items-center gap-1 text-xs", value ? "text-accent-fg" : "text-danger-fg")}
      title={`${label}: ${value ? yes : no}`}
    >
      <Icon aria-hidden className="size-3.5" />
      <span className="sr-only">{`${label}: `}</span>
      <span>{value ? yes : no}</span>
    </span>
  );
}

/** Headline 0–100 score, explained by the caller's sections (spec §37). */
export function ScoreStat({ score, label, caption }: { score: number; label: string; caption: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <span className="label-caps">{label}</span>
      <span className="tabular text-3xl font-semibold text-fg">
        {Math.round(score)}
        <span className="ml-1 text-sm font-normal text-muted">/ 100</span>
      </span>
      <span className="text-xs text-fg-secondary">{caption}</span>
    </div>
  );
}

interface AnalysisQuery<T> {
  data: T | null | undefined;
  isPending: boolean;
  isError: boolean;
  error: unknown;
  refetch: () => unknown;
}

export interface AnalysisSurfaceProps<T extends { architectureVersion: number }> {
  projectId: string;
  /** "Reliability" */
  title: string;
  description: string;
  icon: LucideIcon;
  /** Provenance label in the header, e.g. "Reliability engine". */
  engineLabel: string;
  query: AnalysisQuery<T>;
  /** ISO timestamp of the result. */
  timestamp: (data: T) => string;
  /** "Analyzed" | "Calculated" */
  timestampVerb: string;
  run: {
    onRun: () => void;
    running: boolean;
    /** "Run reliability analysis" */
    idleLabel: string;
    /** "Re-run analysis" */
    rerunLabel: string;
    /** "Analyzing…" */
    pendingLabel: string;
  };
  /** Empty-state copy when the current version has no result yet. */
  emptyDescription: string;
  overlay?: { mode: OverlayMode; label: string };
  skeleton?: React.ReactNode;
  children: (
    data: T,
    context: { architecture: Architecture | null; nodeName: (id: string) => string },
  ) => React.ReactNode;
}

/**
 * Shared container for the OPERATE analysis surfaces (reliability, security, observability,
 * cost). Owns the loading, error, empty and stale states (spec §47–49) so every surface
 * behaves identically; results are rendered by `children` exactly as the backend sent them.
 */
export function AnalysisSurface<T extends { architectureVersion: number }>({
  projectId,
  title,
  description,
  icon,
  engineLabel,
  query,
  timestamp,
  timestampVerb,
  run,
  emptyDescription,
  overlay,
  skeleton,
  children,
}: AnalysisSurfaceProps<T>) {
  const architecture = useArchitecture(projectId);
  const arch = architecture.data ?? null;
  const data = query.data ?? null;

  const nodeName = useCallback((id: string) => (arch ? graphNodeName(arch, id) : id), [arch]);

  const runButton = (
    <Button
      variant={data ? "secondary" : "primary"}
      onClick={run.onRun}
      loading={run.running}
      disabled={!arch}
      className="print:hidden"
    >
      {run.running ? null : data ? (
        <RotateCw aria-hidden className="size-4" />
      ) : (
        <Play aria-hidden className="size-4" />
      )}
      {run.running ? run.pendingLabel : data ? run.rerunLabel : run.idleLabel}
    </Button>
  );

  const overlayLink = overlay ? (
    <Button asChild variant="ghost" className="print:hidden">
      <Link href={locateHref(projectId, { mode: overlay.mode })}>
        <ArrowUpRight aria-hidden className="size-4" />
        {overlay.label}
      </Link>
    </Button>
  ) : null;

  let body: React.ReactNode;
  if (query.isPending || architecture.isPending) {
    body = skeleton ?? <DefaultSkeleton label={`Loading ${title.toLowerCase()} analysis`} />;
  } else if (query.isError) {
    const info = getErrorInfo(query.error);
    body = (
      <ErrorState
        title={`${title} results could not be loaded.`}
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void query.refetch()}
      />
    );
  } else if (architecture.isError && !data) {
    const info = getErrorInfo(architecture.error);
    body = (
      <ErrorState
        title="The architecture could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void architecture.refetch()}
      />
    );
  } else if (!arch && !data) {
    body = (
      <EmptyState
        icon={icon}
        title="No architecture to analyze yet."
        description={`Describe your system and generate an architecture. ${title} is analyzed from its components and connections.`}
        action={
          <Button asChild variant="primary">
            <Link href={`/project/${encodeURIComponent(projectId)}/requirements`}>Describe system</Link>
          </Button>
        }
      />
    );
  } else if (!data) {
    body = (
      <EmptyState
        icon={icon}
        title={`${title} not analyzed yet${arch ? ` for v${arch.version}` : ""}.`}
        description={emptyDescription}
        action={runButton}
      />
    );
  } else {
    const stale = arch !== null && arch.version !== data.architectureVersion;
    body = (
      <>
        {stale ? (
          <Alert
            tone="warning"
            title={`These results are for v${data.architectureVersion}; the architecture is now v${arch.version}.`}
            actions={
              <Button size="sm" onClick={run.onRun} loading={run.running} className="print:hidden">
                Re-run for v{arch.version}
              </Button>
            }
          >
            Results below may not reflect the latest changes.
          </Alert>
        ) : null}
        {children(data, { architecture: arch, nodeName })}
      </>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title={title}
        description={description}
        meta={
          data ? (
            <>
              <ProvenanceTag kind="calculated" label={engineLabel} />
              <span>
                Architecture <span className="tabular">v{data.architectureVersion}</span>
              </span>
              <span aria-hidden>·</span>
              <span>
                {timestampVerb} <time dateTime={timestamp(data)}>{formatDateTime(timestamp(data))}</time>
              </span>
            </>
          ) : undefined
        }
        actions={
          data ? (
            <>
              {overlayLink}
              {runButton}
            </>
          ) : undefined
        }
      />
      {body}
    </div>
  );
}

function DefaultSkeleton({ label }: { label: string }) {
  return (
    <SkeletonGroup label={label} className="flex flex-col gap-6">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        {[0, 1, 2].map((i) => (
          <Skeleton key={i} className="h-24" />
        ))}
      </div>
      <Skeleton className="h-64" />
      <Skeleton className="h-48" />
    </SkeletonGroup>
  );
}
