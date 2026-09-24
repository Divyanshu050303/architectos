"use client";

import { CircleCheck, Play, RotateCw, ShieldCheck, X } from "lucide-react";
import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useMemo } from "react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Alert } from "@/components/ui/alert";
import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import { Select } from "@/components/ui/input";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { toast } from "@/components/ui/toast";
import { HealthPanel } from "@/features/health/components/HealthPanel";
import { useArchitecture } from "@/hooks/use-architecture";
import { useRegisterCommands } from "@/hooks/use-command";
import { useRunValidation, useValidation } from "@/hooks/use-validation";
import { formatDateTime } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import { HEALTH_CATEGORIES, SEVERITIES } from "@/schemas/validation";
import type { Severity } from "@/types/validation";

import { toastError } from "../notify";
import { CATEGORY_LABEL, isHealthCategory, isSeverity, pluralFindings, SEVERITY_META } from "../severity";
import { SeverityGroup } from "./SeverityGroup";

const CATEGORY_OPTIONS = [
  { value: "", label: "All categories" },
  ...HEALTH_CATEGORIES.map((c) => ({ value: c, label: CATEGORY_LABEL[c] })),
];

/**
 * Validation page container (spec §38–39). Filters live in the URL so views are
 * shareable: `?severity=critical,high`, `?category=reliability`, `?ignored=1`.
 */
export function ValidationView({ projectId }: { projectId: string }) {
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const validation = useValidation(projectId);
  const architecture = useArchitecture(projectId);
  const { mutate: runMutation, isPending: running } = useRunValidation(projectId);

  const arch = architecture.data ?? null;
  const report = validation.data ?? null;

  const severityFilter = useMemo(
    () => new Set((searchParams.get("severity") ?? "").split(",").filter(isSeverity)),
    [searchParams],
  );
  const categoryParam = searchParams.get("category") ?? "";
  const category = isHealthCategory(categoryParam) ? categoryParam : null;
  const showIgnored = searchParams.get("ignored") === "1";

  const setParams = useCallback(
    (update: (params: URLSearchParams) => void) => {
      const params = new URLSearchParams(searchParams.toString());
      update(params);
      const query = params.toString();
      router.replace(query ? `${pathname}?${query}` : pathname, { scroll: false });
    },
    [pathname, router, searchParams],
  );

  const toggleSeverity = (severity: Severity) =>
    setParams((params) => {
      const next = new Set(severityFilter);
      if (next.has(severity)) next.delete(severity);
      else next.add(severity);
      const value = SEVERITIES.filter((s) => next.has(s)).join(",");
      if (value) params.set("severity", value);
      else params.delete("severity");
    });

  const clearFilters = () =>
    setParams((params) => {
      params.delete("severity");
      params.delete("category");
    });

  const runValidation = useCallback(() => {
    runMutation(undefined, {
      onSuccess: (result) =>
        toast("Validation complete", {
          tone: "success",
          description: `${pluralFindings(result.findings.length)} for architecture v${result.architectureVersion}.`,
        }),
      onError: (error) => toastError("Validation failed. No changes were applied.", error),
    });
  }, [runMutation]);

  const commands = useMemo(
    () => [
      {
        id: "analysis.validation.run",
        label: "Validate architecture",
        group: "Analysis" as const,
        keywords: ["validation", "findings", "health", "rules", "check"],
        disabled: running || !arch,
        run: runValidation,
      },
    ],
    [running, arch, runValidation],
  );
  useRegisterCommands(commands);

  const runButton = (
    <Button
      variant={report ? "secondary" : "primary"}
      onClick={runValidation}
      loading={running}
      disabled={!arch}
      className="print:hidden"
    >
      {running ? null : report ? (
        <RotateCw aria-hidden className="size-4" />
      ) : (
        <Play aria-hidden className="size-4" />
      )}
      {running ? "Validating…" : report ? "Re-run validation" : "Run validation"}
    </Button>
  );

  let body: React.ReactNode;
  if (validation.isPending || architecture.isPending) {
    body = (
      <SkeletonGroup label="Loading validation results" className="flex flex-col gap-4">
        <Skeleton className="h-16" />
        <Skeleton className="h-40" />
        <Skeleton className="h-40" />
      </SkeletonGroup>
    );
  } else if (validation.isError) {
    const info = getErrorInfo(validation.error);
    body = (
      <ErrorState
        title="Validation results could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void validation.refetch()}
      />
    );
  } else if (!report) {
    body = arch ? (
      <EmptyState
        icon={ShieldCheck}
        title={`No validation yet for v${arch.version}.`}
        description="Run validation to check the architecture against capacity, reliability, security, observability and cost rules."
        action={runButton}
      />
    ) : (
      <EmptyState
        icon={ShieldCheck}
        title="No architecture to validate yet."
        description="Describe your system and generate an architecture, then validate it here."
        action={
          <Button asChild variant="primary">
            <Link href={`/project/${encodeURIComponent(projectId)}/requirements`}>Describe system</Link>
          </Button>
        }
      />
    );
  } else {
    const stale = arch !== null && arch.version !== report.architectureVersion;
    const ignoredCount = report.findings.filter((f) => f.status === "ignored").length;
    const openCount = report.findings.length - ignoredCount;
    const scoped = report.findings.filter(
      (f) => (category === null || f.category === category) && (showIgnored || f.status === "open"),
    );
    const visible = scoped.filter((f) => severityFilter.size === 0 || severityFilter.has(f.severity));
    const filtered = severityFilter.size > 0 || category !== null;

    body = (
      <>
        {stale ? (
          <Alert
            tone="warning"
            title={`These findings are for v${report.architectureVersion}; the architecture is now v${arch.version}.`}
            actions={
              <Button size="sm" onClick={runValidation} loading={running} className="print:hidden">
                Re-validate v{arch.version}
              </Button>
            }
          >
            Findings may not reflect the latest changes.
          </Alert>
        ) : null}

        <div className="grid grid-cols-1 gap-6 lg:grid-cols-[minmax(0,1fr)_300px]">
          <div className="flex min-w-0 flex-col gap-5">
            <div
              role="group"
              aria-label="Filter findings by severity"
              className="grid grid-cols-2 gap-2 sm:grid-cols-5"
            >
              {SEVERITIES.map((severity) => {
                const meta = SEVERITY_META[severity];
                const count = scoped.filter((f) => f.severity === severity).length;
                const pressed = severityFilter.has(severity);
                return (
                  <button
                    key={severity}
                    type="button"
                    aria-pressed={pressed}
                    onClick={() => toggleSeverity(severity)}
                    className={cn(
                      "flex flex-col items-start gap-0.5 rounded-md border px-3 py-2 text-left transition-colors",
                      pressed
                        ? "border-accent-strong bg-accent-soft"
                        : "border-default bg-surface hover:bg-surface-2",
                      // De-emphasise empty severities without lowering text contrast (spec §62).
                      count === 0 && !pressed && "border-dashed bg-surface-2",
                    )}
                  >
                    <span className="flex items-center gap-1.5 text-2xs font-semibold tracking-[0.06em] text-fg uppercase">
                      <meta.Icon aria-hidden className={cn("size-3.5", meta.iconClass)} />
                      {meta.label}
                    </span>
                    <span className="tabular text-xs text-fg-secondary">{pluralFindings(count)}</span>
                  </button>
                );
              })}
            </div>

            <div className="flex flex-wrap items-center gap-3 text-sm">
              <label className="flex items-center gap-2 text-xs text-fg-secondary">
                <span>Category</span>
                <Select
                  aria-label="Filter by category"
                  options={CATEGORY_OPTIONS}
                  value={category ?? ""}
                  onChange={(e) =>
                    setParams((params) => {
                      if (e.target.value) params.set("category", e.target.value);
                      else params.delete("category");
                    })
                  }
                  className="h-7 w-40 text-xs"
                />
              </label>
              <Checkbox
                size="sm"
                checked={showIgnored}
                onCheckedChange={(checked) =>
                  setParams((params) => {
                    if (checked === true) params.set("ignored", "1");
                    else params.delete("ignored");
                  })
                }
                labelClassName="text-xs text-fg-secondary"
                label={
                  <>
                    Show ignored <span className="tabular">({ignoredCount})</span>
                  </>
                }
              />
              {filtered ? (
                <Button size="sm" variant="ghost" onClick={clearFilters}>
                  <X aria-hidden className="size-3.5" />
                  Clear filters
                </Button>
              ) : null}
              <span className="ml-auto text-xs text-muted" aria-live="polite">
                Showing {pluralFindings(visible.length)}
              </span>
            </div>

            {openCount === 0 && !showIgnored ? (
              <EmptyState
                icon={CircleCheck}
                title={`No open findings for v${report.architectureVersion}.`}
                description={
                  ignoredCount > 0
                    ? `The validation engine found no open issues. ${pluralFindings(ignoredCount)} ignored.`
                    : "The validation engine found no issues in this architecture version."
                }
                action={
                  ignoredCount > 0 ? (
                    <Button size="sm" onClick={() => setParams((p) => p.set("ignored", "1"))}>
                      Show ignored
                    </Button>
                  ) : undefined
                }
              />
            ) : visible.length === 0 ? (
              <EmptyState
                title="No findings match these filters."
                description="Change the severity or category filters to see other findings."
                action={
                  <Button size="sm" onClick={clearFilters}>
                    Clear filters
                  </Button>
                }
              />
            ) : (
              SEVERITIES.map((severity) => {
                const findings = visible.filter((f) => f.severity === severity);
                return findings.length > 0 ? (
                  <SeverityGroup
                    key={severity}
                    projectId={projectId}
                    severity={severity}
                    findings={findings}
                  />
                ) : null;
              })
            )}
          </div>

          <aside className="flex flex-col gap-4 lg:sticky lg:top-4 lg:self-start">
            <HealthPanel projectId={projectId} />
          </aside>
        </div>
      </>
    );
  }

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6">
      <PageHeader
        title="Validation"
        description="Findings from the validation engine, grouped by severity."
        meta={
          report ? (
            <>
              <ProvenanceTag kind="finding" label="Validation engine" />
              <span>
                Architecture <span className="tabular">v{report.architectureVersion}</span>
              </span>
              <span aria-hidden>·</span>
              <span>
                Validated <time dateTime={report.validatedAt}>{formatDateTime(report.validatedAt)}</time>
              </span>
            </>
          ) : undefined
        }
        actions={report ? runButton : undefined}
      />
      {body}
    </div>
  );
}
