"use client";

/**
 * Version comparison (spec §43, §93): pick two saved versions and show the backend
 * diff. Opened from the toolbar version menu, the conflict dialog's [Compare], the
 * read-only version banner and the ⌘K "Compare versions" command.
 */
import { ArrowRight } from "lucide-react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Dialog, DialogContent } from "@/components/ui/dialog";
import { Field, Select, type SelectOption } from "@/components/ui/input";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { ComparisonView } from "@/features/evolution/components/ComparisonView";
import { useArchitectureVersions, useCompareVersions } from "@/hooks/use-architecture";
import { useWorkspaceStore } from "@/stores/workspace-store";

function VersionSelect({
  label,
  value,
  versions,
  onChange,
}: {
  label: string;
  value: number | null;
  versions: readonly number[];
  onChange: (version: number) => void;
}) {
  const options: SelectOption[] = versions.map((v) => ({ value: String(v), label: `v${v}` }));
  if (value === null) options.unshift({ value: "", label: "Choose a version", disabled: true });
  return (
    <Field label={label} className="w-32">
      {({ id }) => (
        <Select
          id={id}
          value={value === null ? "" : String(value)}
          options={options}
          onChange={(event) => onChange(Number(event.currentTarget.value))}
          className="tabular"
        />
      )}
    </Field>
  );
}

export function CompareVersionsDialog({ projectId }: { projectId: string }) {
  const compare = useWorkspaceStore((s) => s.compare);
  const versionsQuery = useArchitectureVersions(projectId);
  const versions = (versionsQuery.data ?? []).map((v) => v.version).sort((a, b) => a - b);

  const from = compare?.from ?? null;
  const to = compare?.to ?? null;
  const ready = from !== null && to !== null && from !== to;
  const comparison = useCompareVersions(projectId, ready ? from : null, ready ? to : null);

  const setSide = (side: "from" | "to", version: number) => {
    const current = useWorkspaceStore.getState().compare;
    useWorkspaceStore
      .getState()
      .openCompare(
        side === "from" ? version : (current?.from ?? null),
        side === "to" ? version : (current?.to ?? null),
      );
  };

  return (
    <Dialog
      open={compare !== null}
      onOpenChange={(open) => !open && useWorkspaceStore.getState().closeCompare()}
    >
      <DialogContent
        title="Compare versions"
        description="Differences between two saved versions, from the backend."
        className="motion-dialog max-w-2xl"
      >
        <div className="flex flex-col gap-4">
          <div className="flex items-end gap-3">
            <VersionSelect
              label="From"
              value={from}
              versions={versions}
              onChange={(v) => setSide("from", v)}
            />
            <ArrowRight aria-hidden className="mb-2 size-4 text-muted" />
            <VersionSelect label="To" value={to} versions={versions} onChange={(v) => setSide("to", v)} />
          </div>

          {!ready ? (
            <p className="text-sm text-muted">
              {versions.length < 2
                ? "Only one version has been saved, so there is nothing to compare yet."
                : "Choose two different versions to compare."}
            </p>
          ) : comparison.isPending ? (
            <SkeletonGroup label="Loading comparison" className="flex flex-col gap-2">
              <Skeleton className="h-5 w-40" />
              <Skeleton className="h-4 w-full" />
              <Skeleton className="h-4 w-3/4" />
            </SkeletonGroup>
          ) : comparison.isError ? (
            <ErrorState
              title="The comparison could not be loaded."
              message={getErrorInfo(comparison.error).message}
              requestId={getErrorInfo(comparison.error).requestId}
              onRetry={() => void comparison.refetch()}
            />
          ) : comparison.data ? (
            <ComparisonView comparison={comparison.data} />
          ) : null}
        </div>
      </DialogContent>
    </Dialog>
  );
}
