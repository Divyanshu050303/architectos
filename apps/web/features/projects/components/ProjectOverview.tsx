"use client";

import { ArrowRight, Check, Circle } from "lucide-react";
import Link from "next/link";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge, StatusBadge } from "@/components/ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter } from "@/components/ui/progress";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { projectHref } from "@/config/navigation";
import { HealthPanel } from "@/features/health/components/HealthPanel";
import { useArchitectureVersions } from "@/hooks/use-architecture";
import { useProject } from "@/hooks/use-projects";
import { formatCompact, formatPercent, formatRelativeTime } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type { Project } from "@/types/project";

import { utilizationTone } from "../utils";
import { ProjectLoadError } from "./ProjectLoadError";

export function ProjectOverview({ projectId }: { projectId: string }) {
  const { data: project, isPending, isError, error, refetch } = useProject(projectId);

  if (isPending) {
    return (
      <SkeletonGroup label="Loading project" className="flex flex-col gap-4">
        <Skeleton className="h-7 w-64" />
        <div className="grid gap-4 lg:grid-cols-3">
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
          <Skeleton className="h-40" />
        </div>
      </SkeletonGroup>
    );
  }
  if (isError) return <ProjectLoadError error={error} onRetry={() => void refetch()} />;

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title={project.name}
        description={project.description || undefined}
        meta={
          <>
            <StatusBadge status={project.summary.status} />
            <span>
              Updated <time dateTime={project.updatedAt}>{formatRelativeTime(project.updatedAt)}</time>
            </span>
          </>
        }
      />
      <div className="grid gap-4 lg:grid-cols-3">
        <SummaryCard project={project} />
        <NextSteps project={project} />
        <RecentVersions projectId={projectId} />
      </div>
      <HealthPanel projectId={projectId} />
    </div>
  );
}

function SummaryCard({ project }: { project: Project }) {
  const { summary } = project;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Summary</CardTitle>
      </CardHeader>
      <CardContent className="flex flex-col gap-4">
        <dl className="grid grid-cols-2 gap-x-4 gap-y-3 text-sm">
          <div className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted">Architecture</dt>
            <dd className={project.architectureVersion === null ? "text-muted" : "tabular text-fg"}>
              {project.architectureVersion === null ? "Not generated" : `v${project.architectureVersion}`}
            </dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted">Capacity used</dt>
            <dd className={summary.capacityUtilization === null ? "text-muted" : "tabular text-fg"}>
              {summary.capacityUtilization === null
                ? "Not analyzed"
                : formatPercent(summary.capacityUtilization)}
            </dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted">Daily active users</dt>
            <dd className="tabular text-fg">{formatCompact(summary.dailyActiveUsers)}</dd>
          </div>
          <div className="flex flex-col gap-0.5">
            <dt className="text-xs text-muted">Peak RPS</dt>
            <dd className="tabular text-fg">{formatCompact(summary.peakRps)}</dd>
          </div>
        </dl>
        {summary.capacityUtilization !== null ? (
          <Meter
            value={summary.capacityUtilization}
            label="Highest component utilization"
            tone={utilizationTone(summary.capacityUtilization)}
          />
        ) : null}
        <div className="flex flex-wrap gap-1.5">
          <ProvenanceTag kind="fact" label="DAU / RPS from requirements" />
          {summary.capacityUtilization !== null ? (
            <ProvenanceTag kind="calculated" label="Capacity engine" />
          ) : null}
        </div>
      </CardContent>
    </Card>
  );
}

function NextSteps({ project }: { project: Project }) {
  const hasArchitecture = project.architectureVersion !== null;
  const steps = [
    {
      segment: "requirements",
      label: "Describe requirements",
      detail: "Traffic, latency and availability targets.",
      done: project.summary.dailyActiveUsers !== null || hasArchitecture,
    },
    {
      segment: "architecture",
      label: hasArchitecture ? "Review architecture" : "Generate architecture",
      detail: "Inspect components and connections.",
      done: hasArchitecture,
    },
    {
      segment: "validation",
      label: "Validate",
      detail: "Find single points of failure and risks.",
      // The project summary does not say whether validation ran; the Validation page does.
      done: false,
    },
    {
      segment: "capacity",
      label: "Analyze capacity",
      detail: "See where the system saturates first.",
      done: project.summary.capacityUtilization !== null,
    },
  ];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Next steps</CardTitle>
      </CardHeader>
      <CardContent className="p-2">
        <ol className="flex flex-col">
          {steps.map((step, index) => (
            <li key={step.segment}>
              <Link
                href={projectHref(project.id, step.segment)}
                className="group flex items-center gap-3 rounded-sm px-2 py-2 hover:bg-surface-2"
              >
                <span
                  className={cn(
                    "flex size-5 shrink-0 items-center justify-center rounded-full border text-2xs",
                    step.done
                      ? "border-accent/40 bg-accent-soft text-accent-fg"
                      : "tabular border-default text-muted",
                  )}
                >
                  {step.done ? <Check aria-hidden className="size-3" /> : index + 1}
                </span>
                <span className="flex min-w-0 flex-1 flex-col">
                  <span className="text-sm font-medium text-fg">
                    {step.label}
                    <span className="sr-only">{step.done ? " (done)" : ""}</span>
                  </span>
                  <span className="truncate text-xs text-muted">{step.detail}</span>
                </span>
                <ArrowRight aria-hidden className="size-3.5 text-muted group-hover:text-fg" />
              </Link>
            </li>
          ))}
        </ol>
      </CardContent>
    </Card>
  );
}

function RecentVersions({ projectId }: { projectId: string }) {
  const { data: versions, isPending, isError, error, refetch } = useArchitectureVersions(projectId);
  const recent = versions ? [...versions].sort((a, b) => b.version - a.version).slice(0, 5) : [];
  return (
    <Card>
      <CardHeader>
        <CardTitle>Recent versions</CardTitle>
        <Link
          href={projectHref(projectId, "architecture")}
          className="text-xs text-fg-secondary hover:text-fg"
        >
          Open architecture
        </Link>
      </CardHeader>
      <CardContent className="p-2">
        {isPending ? (
          <SkeletonGroup label="Loading versions" className="flex flex-col gap-2 p-2">
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
            <Skeleton className="h-8" />
          </SkeletonGroup>
        ) : isError ? (
          <ErrorState
            className="m-2"
            title="Versions could not be loaded."
            message={getErrorInfo(error).message}
            requestId={getErrorInfo(error).requestId}
            onRetry={() => void refetch()}
          />
        ) : recent.length === 0 ? (
          <p className="flex items-start gap-2 px-2 py-3 text-sm text-fg-secondary">
            <Circle aria-hidden className="mt-0.5 size-3.5 shrink-0 text-muted" />
            No versions yet. Generating an architecture from your requirements creates v1.
          </p>
        ) : (
          <ul className="flex flex-col">
            {recent.map((v) => (
              <li key={v.version} className="flex items-start gap-3 rounded-sm px-2 py-2">
                <Badge tone="neutral" className="tabular shrink-0">
                  v{v.version}
                </Badge>
                <div className="flex min-w-0 flex-1 flex-col gap-1">
                  <p className="truncate text-sm text-fg">{v.summary || "No summary"}</p>
                  <div className="flex items-center gap-2 text-xs text-muted">
                    {v.createdBy === "ai" ? (
                      <ProvenanceTag kind="ai" label="AI generated" />
                    ) : v.createdBy === "discovery" ? (
                      <ProvenanceTag kind="fact" label="Imported by discovery" />
                    ) : (
                      <ProvenanceTag kind="fact" label="Edited by you" />
                    )}
                    <time dateTime={v.createdAt}>{formatRelativeTime(v.createdAt)}</time>
                  </div>
                </div>
              </li>
            ))}
          </ul>
        )}
      </CardContent>
    </Card>
  );
}
