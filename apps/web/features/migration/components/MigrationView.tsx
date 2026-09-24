"use client";

import { ArrowRight, ArrowUpRight, Clock, ListOrdered, RotateCcw, Route } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Alert } from "@/components/ui/alert";
import { Badge, type BadgeTone } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { projectHref } from "@/config/navigation";
import { RiskBadge } from "@/features/evolution/components/RiskBadge";
import { useArchitecture } from "@/hooks/use-architecture";
import { useEvolution } from "@/hooks/use-evolution";
import { useMigrations } from "@/hooks/use-migrations";
import { formatCompact } from "@/lib/formatting";
import { nodeName as graphNodeName } from "@/lib/graph";
import { cn } from "@/lib/utils";
import type { EvolutionStage, MigrationPlan } from "@/types/evolution";

import { MigrationDependencyGraph } from "./MigrationDependencyGraph";
import { MigrationSteps } from "./MigrationSteps";

const PLAN_STATUS: Record<MigrationPlan["status"], { label: string; tone: BadgeTone }> = {
  draft: { label: "Draft", tone: "neutral" },
  in_progress: { label: "In progress", tone: "info" },
  completed: { label: "Completed", tone: "accent" },
};

/** Migration plans between evolution stages. The selected plan is deep-linkable via `?migration=<id>`. */
export function MigrationView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const migrations = useMigrations(projectId);
  const evolution = useEvolution(projectId);
  const architecture = useArchitecture(projectId);

  const arch = architecture.data ?? null;
  const nodeName = useCallback((id: string) => (arch ? graphNodeName(arch, id) : id), [arch]);
  const stages = evolution.data?.stages ?? [];
  const stageLabel = (id: string) => {
    const stage: EvolutionStage | undefined = stages.find((s) => s.id === id);
    return stage ? `${stage.label} · ${formatCompact(stage.dailyActiveUsers)} DAU` : id;
  };

  const select = (id: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("migration", id);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  let body: React.ReactNode;
  if (migrations.isPending) {
    body = (
      <SkeletonGroup
        label="Loading migration plans"
        className="grid gap-6 lg:grid-cols-[220px_minmax(0,1fr)]"
      >
        <Skeleton className="h-24" />
        <div className="flex flex-col gap-4">
          <Skeleton className="h-28" />
          <Skeleton className="h-40" />
          <Skeleton className="h-80" />
        </div>
      </SkeletonGroup>
    );
  } else if (migrations.isError) {
    const info = getErrorInfo(migrations.error);
    body = (
      <ErrorState
        title="Migration plans could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void migrations.refetch()}
      />
    );
  } else if (migrations.data.length === 0) {
    body = (
      <EmptyState
        icon={Route}
        title="No migration plans yet."
        description="A migration plan is created for each planned evolution stage. Open the evolution roadmap to plan the next stage; its dependency-ordered steps and rollbacks appear here."
        action={
          <Button asChild variant="primary">
            <Link href={projectHref(projectId, "evolution")}>Open evolution roadmap</Link>
          </Button>
        }
      />
    );
  } else {
    const list = migrations.data;
    const requested = searchParams.get("migration");
    const match = list.find((m) => m.id === requested);
    const plan = match ?? (list[0] as MigrationPlan);
    const status = PLAN_STATUS[plan.status];
    const evolutionHref =
      `${projectHref(projectId, "evolution")}?stage=${encodeURIComponent(plan.toStageId)}` +
      `&compare=${encodeURIComponent(`${plan.fromStageId}..${plan.toStageId}`)}`;

    body = (
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[220px_minmax(0,1fr)]">
        <nav aria-label="Migration plans" className="flex flex-col gap-2">
          <h2 className="label-caps">Plans</h2>
          <ul className="flex flex-col gap-1.5">
            {list.map((m) => {
              const active = m.id === plan.id;
              return (
                <li key={m.id}>
                  <button
                    type="button"
                    aria-current={active ? "true" : undefined}
                    onClick={() => select(m.id)}
                    className={cn(
                      "flex w-full flex-col gap-1.5 rounded-md border px-3 py-2.5 text-left",
                      "focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none",
                      active ? "border-strong bg-surface-2" : "border-default bg-surface hover:bg-surface-2",
                    )}
                  >
                    <span className="text-sm font-medium text-fg">{m.title}</span>
                    <span className="flex flex-wrap items-center gap-1.5 text-xs text-fg-secondary">
                      <span>{PLAN_STATUS[m.status].label}</span>
                      <span aria-hidden>·</span>
                      <span className="tabular">{m.steps.length} steps</span>
                      <span aria-hidden>·</span>
                      <span>{m.estimatedDuration}</span>
                    </span>
                  </button>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-col gap-6">
          {requested && !match ? (
            <Alert tone="warning" title="That migration plan was not found.">
              Showing “{plan.title}” instead.
            </Alert>
          ) : null}

          <Card>
            <CardContent className="flex flex-col gap-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="flex min-w-0 flex-col gap-1.5">
                  <h2 className="text-base font-semibold text-fg">{plan.title}</h2>
                  <p className="tabular flex flex-wrap items-center gap-1.5 text-sm text-fg-secondary">
                    <span>{stageLabel(plan.fromStageId)}</span>
                    <ArrowRight aria-hidden className="size-3.5 text-muted" />
                    <span className="sr-only">to</span>
                    <span className="font-medium text-fg">{stageLabel(plan.toStageId)}</span>
                  </p>
                </div>
                <Button asChild variant="secondary" size="sm" className="self-start">
                  <Link href={evolutionHref}>
                    View in evolution
                    <ArrowUpRight aria-hidden className="size-3.5" />
                  </Link>
                </Button>
              </div>
              <dl className="flex flex-wrap gap-x-6 gap-y-3 text-sm">
                <div className="flex flex-col gap-1">
                  <dt className="label-caps">Status</dt>
                  <dd>
                    <Badge tone={status.tone}>{status.label}</Badge>
                  </dd>
                </div>
                <div className="flex flex-col gap-1">
                  <dt className="label-caps">Overall risk</dt>
                  <dd>
                    <RiskBadge risk={plan.overallRisk} />
                  </dd>
                </div>
                <div className="flex flex-col gap-1">
                  <dt className="label-caps">Estimated duration</dt>
                  <dd className="flex items-center gap-1 text-fg">
                    <Clock aria-hidden className="size-3.5 text-muted" />
                    {plan.estimatedDuration}
                  </dd>
                </div>
                <div className="flex flex-col gap-1">
                  <dt className="label-caps">Steps</dt>
                  <dd className="tabular flex items-center gap-1 text-fg">
                    <ListOrdered aria-hidden className="size-3.5 text-muted" />
                    {plan.steps.length}
                  </dd>
                </div>
              </dl>
            </CardContent>
          </Card>

          {plan.steps.length > 0 ? (
            <Card role="region" aria-labelledby="migration-graph-heading">
              <CardHeader>
                <CardTitle id="migration-graph-heading">Dependencies</CardTitle>
              </CardHeader>
              <CardContent>
                <MigrationDependencyGraph steps={plan.steps} />
              </CardContent>
            </Card>
          ) : null}

          <Card role="region" aria-labelledby="migration-steps-heading">
            <CardHeader>
              <CardTitle id="migration-steps-heading">Steps, in dependency order</CardTitle>
            </CardHeader>
            <CardContent>
              {plan.steps.length === 0 ? (
                <p className="text-sm text-fg-secondary">This plan has no steps yet.</p>
              ) : (
                <MigrationSteps steps={plan.steps} nodeName={nodeName} />
              )}
            </CardContent>
          </Card>

          <Card role="region" aria-labelledby="migration-rollback-heading">
            <CardHeader>
              <CardTitle id="migration-rollback-heading" className="flex items-center gap-1.5">
                <RotateCcw aria-hidden className="size-3.5" />
                Rollback plan
              </CardTitle>
            </CardHeader>
            <CardContent>
              <p className="text-sm text-fg">{plan.rollbackPlan}</p>
            </CardContent>
          </Card>
        </div>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Migration"
        description="Dependency-ordered steps to move the architecture from one evolution stage to the next, each with its own rollback."
      />
      {body}
    </div>
  );
}
