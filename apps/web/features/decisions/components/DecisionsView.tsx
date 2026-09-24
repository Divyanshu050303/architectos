"use client";

import { Plus, ScrollText } from "lucide-react";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useState } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useArchitecture } from "@/hooks/use-architecture";
import { useDecisions } from "@/hooks/use-decisions";
import { nodeName as graphNodeName } from "@/lib/graph";

import { CreateDecisionDialog } from "./CreateDecisionDialog";
import { DecisionDetail } from "./DecisionDetail";
import { DecisionList } from "./DecisionList";

/** ADR list + detail. The selected ADR is deep-linkable via `?adr=<id>` (spec §52). */
export function DecisionsView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const decisions = useDecisions(projectId);
  const architecture = useArchitecture(projectId);
  const [creating, setCreating] = useState(false);

  const arch = architecture.data ?? null;
  const nodeName = useCallback((id: string) => (arch ? graphNodeName(arch, id) : id), [arch]);

  const select = (id: string) => {
    const params = new URLSearchParams(searchParams.toString());
    params.set("adr", id);
    router.replace(`${pathname}?${params.toString()}`, { scroll: false });
  };

  const newButton = (
    <Button variant="primary" onClick={() => setCreating(true)}>
      <Plus aria-hidden className="size-4" />
      New ADR
    </Button>
  );

  let body: React.ReactNode;
  if (decisions.isPending) {
    body = (
      <SkeletonGroup label="Loading decisions" className="grid gap-6 md:grid-cols-[300px_minmax(0,1fr)]">
        <div className="flex flex-col gap-2">
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16" />
          ))}
        </div>
        <Skeleton className="h-80" />
      </SkeletonGroup>
    );
  } else if (decisions.isError) {
    const info = getErrorInfo(decisions.error);
    body = (
      <ErrorState
        title="Decisions could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void decisions.refetch()}
      />
    );
  } else if (decisions.data.length === 0) {
    body = (
      <EmptyState
        icon={ScrollText}
        title="No decisions recorded yet."
        description="Record why the architecture looks the way it does. Create an ADR here, or from any validation finding."
        action={newButton}
      />
    );
  } else {
    const list = decisions.data;
    const requested = searchParams.get("adr");
    const selected =
      list.find((d) => d.id === requested) ?? [...list].sort((a, b) => a.number - b.number)[0] ?? null;
    body = (
      <div className="grid gap-6 md:grid-cols-[300px_minmax(0,1fr)]">
        <DecisionList decisions={list} selectedId={selected?.id ?? null} onSelect={select} />
        <Card>
          <CardContent className="p-5">
            {selected ? (
              <DecisionDetail projectId={projectId} decision={selected} nodeName={nodeName} />
            ) : null}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Decisions"
        description="Architecture decision records: what was decided, why, and what it costs."
        actions={decisions.data && decisions.data.length > 0 ? newButton : undefined}
      />
      {body}
      <CreateDecisionDialog projectId={projectId} open={creating} onOpenChange={setCreating} />
    </div>
  );
}
