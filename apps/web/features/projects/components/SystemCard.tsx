import { AlertTriangle } from "lucide-react";
import Link from "next/link";

import { StatusBadge } from "@/components/ui/badge";
import { Meter } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import { projectHref } from "@/config/navigation";
import { formatCompact, formatPercent } from "@/lib/formatting";
import type { Project } from "@/types/project";

import { utilizationTone } from "../utils";

/** One system on the dashboard (spec §46). */
export function SystemCard({ project }: { project: Project }) {
  const { summary } = project;
  const utilization = summary.capacityUtilization;
  return (
    <Link
      href={projectHref(project.id)}
      className="group flex flex-col gap-4 rounded-md border border-default bg-surface p-4 transition-colors hover:border-strong"
    >
      <div className="flex items-start justify-between gap-3">
        <div className="flex min-w-0 flex-col gap-1">
          <h3 className="truncate text-sm font-semibold text-fg">{project.name}</h3>
          {project.description ? (
            <p className="line-clamp-1 text-xs text-muted">{project.description}</p>
          ) : null}
        </div>
        <StatusBadge status={summary.status} className="shrink-0" />
      </div>

      <dl className="grid grid-cols-2 gap-3">
        <div className="flex flex-col gap-0.5">
          <dt className="label-caps">DAU</dt>
          <dd className="tabular text-base text-fg">{formatCompact(summary.dailyActiveUsers)}</dd>
        </div>
        <div className="flex flex-col gap-0.5">
          <dt className="label-caps">Peak RPS</dt>
          <dd className="tabular text-base text-fg">{formatCompact(summary.peakRps)}</dd>
        </div>
      </dl>

      <div className="flex flex-col gap-1.5">
        <div className="flex items-center justify-between text-xs">
          <span className="text-fg-secondary">Capacity</span>
          {/* Mono is for numbers only (spec typography); the placeholder text stays in the UI font. */}
          {utilization === null ? (
            <span className="text-muted">Not analyzed</span>
          ) : (
            <span className="tabular text-fg">{formatPercent(utilization)}</span>
          )}
        </div>
        {utilization === null ? (
          <Meter value={0} label={`${project.name} capacity: not analyzed`} tone="neutral" />
        ) : (
          <Meter
            value={utilization}
            label={`${project.name} capacity utilization`}
            tone={utilizationTone(utilization)}
          />
        )}
      </div>

      <div className="mt-auto min-h-5 text-xs">
        {summary.topIssue ? (
          <p className="flex items-start gap-1.5 text-warning-fg">
            <AlertTriangle aria-hidden className="mt-px size-3.5 shrink-0" />
            <span className="sr-only">Top issue: </span>
            <span className="line-clamp-2">{summary.topIssue}</span>
          </p>
        ) : project.architectureVersion === null ? (
          <p className="text-muted">No architecture yet — describe requirements to generate one.</p>
        ) : (
          <p className="text-muted">No open issues reported.</p>
        )}
      </div>
    </Link>
  );
}

export function SystemCardSkeleton() {
  return (
    <div aria-hidden className="flex flex-col gap-4 rounded-md border border-default bg-surface p-4">
      <div className="flex justify-between">
        <Skeleton className="h-4 w-32" />
        <Skeleton className="h-5 w-16 rounded-full" />
      </div>
      <div className="grid grid-cols-2 gap-3">
        <Skeleton className="h-9" />
        <Skeleton className="h-9" />
      </div>
      <Skeleton className="h-5" />
      <Skeleton className="h-4 w-3/4" />
    </div>
  );
}
