"use client";

import { ErrorState } from "@/components/feedback/ErrorState";
import { ProvenanceTag, type ProvenanceKind } from "@/components/feedback/ProvenanceTag";
import { Drawer, DrawerContent } from "@/components/ui/drawer";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { getErrorInfo } from "@/api/client";
import { useEvidence } from "@/hooks/use-evidence";
import { formatNumber } from "@/lib/formatting";
import { useUiStore } from "@/stores/ui-store";
import type { Evidence } from "@/types/architecture";

const KIND_TAG: Record<Evidence["kind"], { kind: ProvenanceKind; label: string }> = {
  calculation: { kind: "calculated", label: "Calculation" },
  constraint: { kind: "evidence", label: "Component constraint" },
  rule: { kind: "evidence", label: "Validation rule" },
  benchmark: { kind: "evidence", label: "Benchmark" },
};

/**
 * The single evidence drawer (spec §34). Opened from any "Why?" through the UI store;
 * mount it once per page tree.
 */
export function EvidenceDrawer() {
  const evidenceId = useUiStore((s) => s.evidenceId);
  const closeEvidence = useUiStore((s) => s.closeEvidence);

  return (
    <Drawer open={evidenceId !== null} onOpenChange={(open) => (open ? undefined : closeEvidence())}>
      <DrawerContent title="Evidence" description="Why ArchitectOS reports this value." tone="evidence">
        {evidenceId ? <EvidenceBody evidenceId={evidenceId} /> : null}
      </DrawerContent>
    </Drawer>
  );
}

function EvidenceBody({ evidenceId }: { evidenceId: string }) {
  const { data, isPending, isError, error, refetch } = useEvidence(evidenceId);

  if (isPending) {
    return (
      <SkeletonGroup label="Loading evidence" className="flex flex-col gap-5">
        <Skeleton className="h-5 w-24" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-24 w-full" />
        <Skeleton className="h-8 w-2/3" />
      </SkeletonGroup>
    );
  }

  if (isError) {
    const info = getErrorInfo(error);
    return (
      <ErrorState
        title="Evidence could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void refetch()}
      />
    );
  }

  const tag = KIND_TAG[data.kind];
  return (
    <article className="flex flex-col gap-5" aria-label="Evidence details">
      <div className="flex flex-wrap items-center gap-2">
        <ProvenanceTag kind={tag.kind} label={tag.label} />
        <span className="tabular text-2xs text-muted">{data.id}</span>
      </div>

      <Section title="Claim">
        <p className="text-sm font-medium text-fg">{data.claim}</p>
      </Section>

      <Section title="Calculation">
        {data.calculations.length > 0 ? (
          <dl className="flex flex-col divide-y divide-default rounded-md border border-default bg-surface">
            {data.calculations.map((row, index) => (
              <div
                key={`${row.label}-${index}`}
                className="flex items-baseline justify-between gap-4 px-3 py-2"
              >
                <dt className="text-sm text-fg-secondary">{row.label}</dt>
                <dd className="tabular text-sm text-fg">
                  {typeof row.value === "number" ? formatNumber(row.value) : row.value}
                  {row.unit ? <span className="ml-1 text-muted">{row.unit}</span> : null}
                </dd>
              </div>
            ))}
          </dl>
        ) : (
          <p className="text-sm text-muted">No calculation steps were recorded.</p>
        )}
      </Section>

      <Section title="Source">
        <p className="text-sm text-fg">{data.source}</p>
      </Section>

      <Section title="Assumptions">
        {data.assumptions.length > 0 ? (
          <ul className="flex flex-col gap-2">
            {data.assumptions.map((assumption) => (
              <li key={assumption.id} className="flex gap-3 text-sm">
                <span className="tabular shrink-0 text-xs text-fg-secondary">{assumption.id}</span>
                <span className="text-fg">{assumption.statement}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="text-sm text-muted">No assumptions.</p>
        )}
      </Section>
    </article>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <section className="flex flex-col gap-2">
      <h3 className="label-caps">{title}</h3>
      {children}
    </section>
  );
}
