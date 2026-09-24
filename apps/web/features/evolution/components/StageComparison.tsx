"use client";

import { ArrowRight } from "lucide-react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Field, Select } from "@/components/ui/input";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useCompareStages } from "@/hooks/use-evolution";
import { formatCompact } from "@/lib/formatting";
import type { EvolutionStage } from "@/types/evolution";

import { ComparisonView } from "./ComparisonView";

export interface StageComparisonProps {
  projectId: string;
  stages: readonly EvolutionStage[];
  fromId: string;
  toId: string;
  onChange: (fromId: string, toId: string) => void;
}

/** "Compare V1 vs V2" (spec §43): the diff itself comes from the backend. */
export function StageComparison({ projectId, stages, fromId, toId, onChange }: StageComparisonProps) {
  const same = fromId === toId;
  const comparison = useCompareStages(projectId, same ? null : fromId, same ? null : toId);
  const options = stages.map((s) => ({
    value: s.id,
    label: `${s.label} · ${formatCompact(s.dailyActiveUsers)} DAU`,
  }));

  let body: React.ReactNode;
  if (same) {
    body = <p className="text-sm text-fg-secondary">Choose two different stages to compare.</p>;
  } else if (comparison.isPending) {
    body = (
      <SkeletonGroup label="Loading comparison" className="flex flex-col gap-3">
        <Skeleton className="h-5 w-32" />
        <Skeleton className="h-32" />
        <Skeleton className="h-16" />
      </SkeletonGroup>
    );
  } else if (comparison.isError) {
    const info = getErrorInfo(comparison.error);
    body = (
      <ErrorState
        title="The comparison could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void comparison.refetch()}
      />
    );
  } else {
    body = <ComparisonView comparison={comparison.data} />;
  }

  return (
    <Card role="region" aria-labelledby="stage-compare-heading">
      <CardHeader>
        <CardTitle id="stage-compare-heading">Compare stages</CardTitle>
        <ProvenanceTag kind="calculated" />
      </CardHeader>
      <CardContent className="flex flex-col gap-5">
        <div className="flex flex-wrap items-end gap-3">
          <Field label="From" className="w-full sm:w-52">
            {({ id }) => (
              <Select
                id={id}
                value={fromId}
                options={options}
                onChange={(e) => onChange(e.target.value, toId)}
              />
            )}
          </Field>
          <ArrowRight aria-hidden className="mb-2 hidden size-4 text-muted sm:block" />
          <Field label="To" className="w-full sm:w-52">
            {({ id }) => (
              <Select
                id={id}
                value={toId}
                options={options}
                onChange={(e) => onChange(fromId, e.target.value)}
              />
            )}
          </Field>
        </div>
        {body}
      </CardContent>
    </Card>
  );
}
