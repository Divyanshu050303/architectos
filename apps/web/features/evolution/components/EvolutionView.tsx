"use client";

import { GitBranch } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useId, useMemo } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { projectHref } from "@/config/navigation";
import { useRegisterCommands } from "@/hooks/use-command";
import { useEvolution } from "@/hooks/use-evolution";
import type { EvolutionStage } from "@/types/evolution";

import { EvolutionTimeline, stageTabId } from "./EvolutionTimeline";
import { StageCard } from "./StageCard";
import { StageComparison } from "./StageComparison";

/** Currency is not part of the roadmap contract yet; stage costs are in USD. */
const ROADMAP_CURRENCY = "USD";

function defaultStage(stages: readonly EvolutionStage[]): EvolutionStage | undefined {
  return stages.find((s) => s.status === "current") ?? stages[0];
}

/** Adjacent pair around the selected stage: previous → selected, or selected → next. */
function defaultCompare(stages: readonly EvolutionStage[], selectedId: string): [string, string] | null {
  const index = stages.findIndex((s) => s.id === selectedId);
  const prev = stages[index - 1];
  const current = stages[index];
  const next = stages[index + 1];
  if (prev && current) return [prev.id, current.id];
  if (current && next) return [current.id, next.id];
  return null;
}

function parseCompare(value: string | null, stages: readonly EvolutionStage[]): [string, string] | null {
  if (!value) return null;
  const [from, to] = value.split("..");
  const known = (id: string | undefined): id is string => stages.some((s) => s.id === id);
  return known(from) && known(to) ? [from, to] : null;
}

/**
 * Evolution roadmap (spec §42–43). Deep-linkable (spec §52):
 *   ?stage=<stageId>            selected stage
 *   ?compare=<fromId>..<toId>   stage comparison
 */
export function EvolutionView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const evolution = useEvolution(projectId);
  const idPrefix = useId();
  const panelId = `${idPrefix}-panel`;

  const stages = useMemo(() => evolution.data?.stages ?? [], [evolution.data]);

  const setParams = useCallback(
    (patch: Record<string, string | null>) => {
      const params = new URLSearchParams(searchParams.toString());
      for (const [key, value] of Object.entries(patch)) {
        if (value === null) params.delete(key);
        else params.set(key, value);
      }
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  const requestedStage = stages.find((s) => s.id === searchParams.get("stage"));
  const selected = requestedStage ?? defaultStage(stages);
  const compare =
    parseCompare(searchParams.get("compare"), stages) ??
    (selected ? defaultCompare(stages, selected.id) : null);

  const commands = compare
    ? [
        {
          id: "evolution.compare",
          label: "Compare versions",
          group: "Analysis" as const,
          keywords: ["evolution", "stage", "diff", "compare"],
          run: () => document.getElementById("stage-compare-heading")?.scrollIntoView({ block: "start" }),
        },
      ]
    : [];
  useRegisterCommands(commands);

  let body: React.ReactNode;
  if (evolution.isPending) {
    body = <EvolutionSkeleton />;
  } else if (evolution.isError) {
    const info = getErrorInfo(evolution.error);
    body = (
      <ErrorState
        title="The evolution roadmap could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void evolution.refetch()}
      />
    );
  } else if (!selected) {
    body = (
      <EmptyState
        icon={GitBranch}
        title="No evolution roadmap yet."
        description="ArchitectOS plans growth stages from your scale targets and the capacity envelope. Set expected growth in requirements and run capacity analysis to see where the architecture needs to change next."
        action={
          <>
            <Button asChild variant="primary">
              <Link href={projectHref(projectId, "requirements")}>Edit requirements</Link>
            </Button>
            <Button asChild variant="secondary">
              <Link href={projectHref(projectId, "capacity")}>View capacity</Link>
            </Button>
          </>
        }
      />
    );
  } else {
    body = (
      <>
        <Card>
          <CardContent className="flex flex-col gap-4">
            <EvolutionTimeline
              stages={stages}
              selectedId={selected.id}
              onSelect={(id) => setParams({ stage: id, compare: null })}
              panelId={panelId}
              idPrefix={idPrefix}
            />
            <div
              role="tabpanel"
              id={panelId}
              aria-labelledby={stageTabId(idPrefix, selected.id)}
              className="border-t border-default pt-4"
            >
              <StageCard projectId={projectId} stage={selected} currency={ROADMAP_CURRENCY} />
            </div>
          </CardContent>
        </Card>

        {compare ? (
          <StageComparison
            projectId={projectId}
            stages={stages}
            fromId={compare[0]}
            toId={compare[1]}
            onChange={(from, to) => setParams({ compare: `${from}..${to}` })}
          />
        ) : null}
      </>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Evolution"
        description="How the architecture changes as the system grows: what triggers each stage, what changes, and what it costs."
        meta={
          stages.length > 0 ? (
            <span>
              <span className="tabular">{stages.length}</span> stages
            </span>
          ) : undefined
        }
      />
      {body}
    </div>
  );
}

function EvolutionSkeleton() {
  return (
    <SkeletonGroup label="Loading evolution roadmap" className="flex flex-col gap-6">
      <Skeleton className="h-32" />
      <Skeleton className="h-72" />
      <Skeleton className="h-48" />
    </SkeletonGroup>
  );
}
