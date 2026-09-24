"use client";

import { ChevronRight, HeartPulse } from "lucide-react";
import Link from "next/link";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Meter, type MeterTone } from "@/components/ui/progress";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { CATEGORY_LABEL, pluralFindings, SEVERITY_META, worstSeverity } from "@/features/validation/severity";
import { useValidation } from "@/hooks/use-validation";
import { formatDateTime } from "@/lib/formatting";
import { cn } from "@/lib/utils";
import type { Finding, Severity } from "@/types/validation";

/** Colour follows the worst open finding behind a score, never an arbitrary score cut-off. */
const TONE: Record<Severity, MeterTone> = {
  critical: "danger",
  high: "warning",
  medium: "warning",
  low: "neutral",
  info: "info",
};

/**
 * Explainable system health (spec §37): every score shows the findings it is based on
 * and links to them on the validation page.
 */
export function HealthPanel({ projectId }: { projectId: string }) {
  const validation = useValidation(projectId);
  const base = `/project/${encodeURIComponent(projectId)}`;

  let content: React.ReactNode;
  if (validation.isPending) {
    content = (
      <SkeletonGroup label="Loading system health" className="flex flex-col gap-3">
        <Skeleton className="h-10 w-24" />
        {[0, 1, 2, 3, 4].map((i) => (
          <Skeleton key={i} className="h-12" />
        ))}
      </SkeletonGroup>
    );
  } else if (validation.isError) {
    const info = getErrorInfo(validation.error);
    content = (
      <ErrorState
        title="Health could not be loaded."
        message={info.message}
        requestId={info.requestId}
        onRetry={() => void validation.refetch()}
      />
    );
  } else if (!validation.data) {
    content = (
      <EmptyState
        icon={HeartPulse}
        title="Health not calculated yet."
        description="Validate the architecture to score capacity, reliability, security, observability and cost, each backed by findings."
        action={
          <Button asChild size="sm" variant="primary">
            <Link href={`${base}/validation`}>Go to validation</Link>
          </Button>
        }
        className="py-6"
      />
    );
  } else {
    const report = validation.data;
    const byId = new Map<string, Finding>(report.findings.map((f) => [f.id, f]));
    content = (
      <div className="flex flex-col gap-4">
        <div className="flex flex-col gap-1">
          <p className="flex items-baseline gap-1.5">
            <span className="tabular text-3xl font-semibold text-fg">{report.health.overall}</span>
            <span className="tabular text-xs text-muted">/ 100</span>
            <span className="sr-only">overall health score</span>
          </p>
          <p className="text-xs text-fg-secondary">
            Based on {pluralFindings(report.findings.length)} from validation of{" "}
            <span className="tabular">v{report.architectureVersion}</span>,{" "}
            <time dateTime={report.validatedAt}>{formatDateTime(report.validatedAt)}</time>.
          </p>
        </div>

        <ul className="flex flex-col gap-1">
          {report.health.categories.map((category) => {
            const findings = category.findingIds
              .map((id) => byId.get(id))
              .filter((f): f is Finding => f !== undefined);
            const open = findings.filter((f) => f.status === "open");
            const worst = worstSeverity(open);
            const label = CATEGORY_LABEL[category.category];
            return (
              <li key={category.category}>
                <Link
                  href={`${base}/validation?category=${category.category}`}
                  className="group flex flex-col gap-1.5 rounded-md px-2 py-2 hover:bg-surface-2"
                >
                  <span className="flex items-center justify-between gap-2">
                    <span className="text-sm font-medium text-fg">{label}</span>
                    <span className="flex items-center gap-1">
                      <span className="tabular text-sm text-fg">{category.score}</span>
                      <ChevronRight aria-hidden className="size-3.5 text-muted group-hover:text-fg" />
                    </span>
                  </span>
                  <Meter
                    value={category.score / 100}
                    tone={worst ? TONE[worst] : "accent"}
                    label={`${label} score ${category.score} of 100`}
                  />
                  <span className="text-xs text-fg-secondary">{category.summary}</span>
                  <span className="flex items-center gap-1 text-2xs text-muted">
                    {worst ? (
                      (() => {
                        const meta = SEVERITY_META[worst];
                        return (
                          <>
                            <meta.Icon aria-hidden className={cn("size-3", meta.iconClass)} />
                            {pluralFindings(open.length)} open · worst {meta.label.toLowerCase()}
                          </>
                        );
                      })()
                    ) : (
                      <>Based on {pluralFindings(findings.length)} · none open</>
                    )}
                  </span>
                </Link>
              </li>
            );
          })}
        </ul>
      </div>
    );
  }

  return (
    <Card role="region" aria-labelledby="system-health-heading">
      <CardHeader>
        <CardTitle id="system-health-heading">System health</CardTitle>
        <ProvenanceTag kind="calculated" label="Validation engine" />
      </CardHeader>
      <CardContent>{content}</CardContent>
    </Card>
  );
}
