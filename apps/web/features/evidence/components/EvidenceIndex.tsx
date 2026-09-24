"use client";

import { FileSearch, Search, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo, useState } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { IconButton } from "@/components/ui/icon-button";
import { Input } from "@/components/ui/input";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useEvidenceList } from "@/hooks/use-evidence";
import { cn } from "@/lib/utils";
import type { Evidence } from "@/types/architecture";

import { EVIDENCE_KIND_META, EvidenceDetail } from "./EvidenceDetail";

const KIND_ORDER: readonly Evidence["kind"][] = ["calculation", "constraint", "rule", "benchmark"];

const KIND_GROUP_LABEL: Record<Evidence["kind"], string> = {
  calculation: "Calculations",
  constraint: "Component constraints",
  rule: "Rules",
  benchmark: "Benchmarks",
};

function matches(evidence: Evidence, needle: string): boolean {
  if (!needle) return true;
  const haystack = [
    evidence.id,
    evidence.claim,
    evidence.source,
    evidence.kind,
    ...evidence.assumptions.map((a) => a.statement),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(needle);
}

/**
 * Evidence explorer (spec §34, §52): every evidence record cited by the project's analyses,
 * grouped by kind and searchable. `?evidence=<id>` selects a record.
 */
export function EvidenceIndex({ projectId }: { projectId: string }) {
  const list = useEvidenceList(projectId);
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const selectedId = searchParams.get("evidence");
  const [query, setQuery] = useState("");

  const select = useCallback(
    (id: string | null) => {
      const params = new URLSearchParams(searchParams.toString());
      if (id) params.set("evidence", id);
      else params.delete("evidence");
      const qs = params.toString();
      router.replace(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
    },
    [router, pathname, searchParams],
  );

  const records = useMemo(() => list.data ?? [], [list.data]);
  const needle = query.trim().toLowerCase();
  const groups = useMemo(() => {
    const filtered = records.filter((e) => matches(e, needle));
    return KIND_ORDER.map((kind) => ({ kind, items: filtered.filter((e) => e.kind === kind) })).filter(
      (g) => g.items.length > 0,
    );
  }, [records, needle]);
  const shownCount = groups.reduce((n, g) => n + g.items.length, 0);
  const selected = selectedId ? (records.find((e) => e.id === selectedId) ?? null) : null;

  let body: React.ReactNode;
  if (list.isPending) {
    body = (
      <SkeletonGroup
        label="Loading evidence"
        className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]"
      >
        <div className="flex flex-col gap-2">
          <Skeleton className="h-8" />
          {[0, 1, 2, 3, 4].map((i) => (
            <Skeleton key={i} className="h-14" />
          ))}
        </div>
        <Skeleton className="h-80" />
      </SkeletonGroup>
    );
  } else if (list.isError) {
    const info = getErrorInfo(list.error);
    body = (
      <ErrorState
        title="Evidence could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void list.refetch()}
      />
    );
  } else if (records.length === 0) {
    const base = `/project/${encodeURIComponent(projectId)}`;
    body = (
      <EmptyState
        icon={FileSearch}
        title="No evidence recorded yet."
        description="Evidence is produced when analyses run. Analyze capacity, reliability or security, or calculate cost, and every claim they make will be listed here."
        action={
          <>
            <Button asChild variant="primary">
              <Link href={`${base}/capacity`}>Analyze capacity</Link>
            </Button>
            <Button asChild>
              <Link href={`${base}/cost`}>Calculate cost</Link>
            </Button>
          </>
        }
      />
    );
  } else {
    body = (
      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <div className="flex min-w-0 flex-col gap-3">
          <div className="relative">
            <Search aria-hidden className="pointer-events-none absolute top-2 left-2.5 size-4 text-muted" />
            <Input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Search claims, sources, assumptions"
              aria-label="Search evidence"
              className="pl-8"
            />
          </div>
          <p className="text-xs text-muted" aria-live="polite">
            {shownCount === records.length
              ? `${records.length} ${records.length === 1 ? "record" : "records"}`
              : `${shownCount} of ${records.length} records match`}
          </p>
          {groups.length === 0 ? (
            <p className="rounded-md border border-dashed border-default px-4 py-6 text-center text-sm text-fg-secondary">
              No evidence matches “{query.trim()}”. Try a component name, a rule id or a source.
            </p>
          ) : (
            groups.map((group) => (
              <section
                key={group.kind}
                aria-labelledby={`evidence-group-${group.kind}`}
                className="flex flex-col gap-1.5"
              >
                <h2 id={`evidence-group-${group.kind}`} className="label-caps flex items-center gap-2">
                  {KIND_GROUP_LABEL[group.kind]}
                  <span className="tabular font-normal text-muted">{group.items.length}</span>
                </h2>
                <ul className="flex flex-col gap-1.5">
                  {group.items.map((evidence) => {
                    const active = evidence.id === selectedId;
                    const meta = EVIDENCE_KIND_META[evidence.kind];
                    return (
                      <li key={evidence.id}>
                        <button
                          type="button"
                          aria-current={active ? "true" : undefined}
                          onClick={() => select(evidence.id)}
                          className={cn(
                            "flex w-full flex-col gap-1 rounded-md border px-3 py-2 text-left transition-colors",
                            "focus-visible:ring-2 focus-visible:ring-accent/30 focus-visible:outline-none",
                            active
                              ? "border-accent/40 bg-accent-soft"
                              : "border-default bg-surface hover:bg-surface-2",
                          )}
                        >
                          <span className="text-sm font-medium text-fg">{evidence.claim}</span>
                          <span className="flex flex-wrap items-center gap-2">
                            <Badge tone={evidence.kind === "calculation" ? "info" : "neutral"}>
                              {meta.label}
                            </Badge>
                            <span className="truncate text-xs text-muted">{evidence.source}</span>
                          </span>
                        </button>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))
          )}
        </div>

        <Card
          role="region"
          aria-labelledby="evidence-detail-heading"
          className="self-start lg:sticky lg:top-4"
        >
          <CardHeader>
            <CardTitle id="evidence-detail-heading">Details</CardTitle>
            {selected ? (
              <IconButton label="Close details" size="sm" onClick={() => select(null)}>
                <X aria-hidden className="size-4" />
              </IconButton>
            ) : null}
          </CardHeader>
          <CardContent>
            {selected ? (
              <EvidenceDetail evidence={selected} />
            ) : selectedId ? (
              <p className="text-sm text-fg-secondary">
                Evidence <span className="tabular">{selectedId}</span> is not part of this project. Select a
                record from the list.
              </p>
            ) : (
              <p className="text-sm text-fg-secondary">
                Select a record to see its calculation, source and assumptions.
              </p>
            )}
          </CardContent>
        </Card>
      </div>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Evidence"
        description="Every calculation, constraint, rule and benchmark behind the numbers ArchitectOS reports."
        meta={records.length > 0 ? <ProvenanceTag kind="evidence" label="Backend evidence" /> : undefined}
      />
      {body}
    </div>
  );
}
