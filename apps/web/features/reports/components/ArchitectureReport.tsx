"use client";

import { Printer } from "lucide-react";
import { useCallback, useMemo } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useArchitecture } from "@/hooks/use-architecture";
import { useCapacity } from "@/hooks/use-capacity";
import { useRegisterCommands } from "@/hooks/use-command";
import { useDecisions } from "@/hooks/use-decisions";
import { useProject } from "@/hooks/use-projects";
import { useRequirements } from "@/hooks/use-requirements";
import { useValidation } from "@/hooks/use-validation";
import { formatDateTime } from "@/lib/formatting";

import { CapacitySection, DecisionsSection, HealthSection } from "./AnalysisSections";
import {
  AssumptionsSection,
  ComponentsSection,
  ConnectionsSection,
  RequirementsSection,
} from "./ArchitectureSections";

/** A printable, single-page summary of the project's architecture and analysis. */
export function ArchitectureReport({ projectId }: { projectId: string }) {
  const project = useProject(projectId);
  const requirements = useRequirements(projectId);
  const architecture = useArchitecture(projectId);
  const capacity = useCapacity(projectId);
  const validation = useValidation(projectId);
  const decisions = useDecisions(projectId);

  const print = useCallback(() => window.print(), []);
  const commands = useMemo(
    () => [
      {
        id: "report.export",
        label: "Export report",
        group: "Project" as const,
        keywords: ["print", "pdf", "report", "export"],
        run: print,
      },
    ],
    [print],
  );
  useRegisterCommands(commands);

  if (project.isPending || architecture.isPending) {
    return (
      <SkeletonGroup
        label="Loading report"
        className="mx-auto flex w-full max-w-4xl flex-col gap-6 p-4 sm:p-6"
      >
        <Skeleton className="h-12 w-80" />
        <Skeleton className="h-32" />
        <Skeleton className="h-48" />
        <Skeleton className="h-48" />
      </SkeletonGroup>
    );
  }

  if (project.isError) {
    const info = getErrorInfo(project.error);
    return (
      <div className="mx-auto w-full max-w-4xl p-4 sm:p-6">
        <ErrorState
          title="The report could not be built."
          message={info.message}
          requestId={info.requestId}
          onRetry={() => void project.refetch()}
        />
      </div>
    );
  }

  const arch = architecture.data ?? null;
  const reqs = requirements.data ?? null;
  const cap = capacity.data ?? null;
  const report = validation.data ?? null;
  const adrs = [...(decisions.data ?? [])].sort((a, b) => a.number - b.number);

  return (
    <article
      aria-labelledby="report-title"
      className="mx-auto flex w-full max-w-4xl flex-col gap-8 p-4 text-fg sm:p-6 print:max-w-none print:p-0 print:text-[11px]"
    >
      <header className="flex flex-col gap-3 border-b border-default pb-4 sm:flex-row sm:items-start sm:justify-between">
        <div className="flex flex-col gap-1">
          <p className="label-caps">Architecture report</p>
          <h1 id="report-title" className="text-xl font-semibold">
            {project.data.name}
          </h1>
          {project.data.description ? (
            <p className="text-sm text-fg-secondary">{project.data.description}</p>
          ) : null}
          <p className="flex flex-wrap items-center gap-2 text-xs text-muted">
            {arch ? (
              <>
                <span>
                  Architecture <span className="tabular">v{arch.version}</span>
                </span>
                <span aria-hidden>·</span>
                <span>
                  Saved <time dateTime={arch.createdAt}>{formatDateTime(arch.createdAt)}</time>
                </span>
              </>
            ) : (
              <span>No architecture yet</span>
            )}
          </p>
        </div>
        <Button onClick={print} className="print:hidden">
          <Printer aria-hidden className="size-4" />
          Print / Save as PDF
        </Button>
      </header>

      <RequirementsSection pending={requirements.isPending} reqs={reqs} />
      <ComponentsSection arch={arch} />
      <ConnectionsSection arch={arch} />
      <AssumptionsSection arch={arch} />
      <CapacitySection pending={capacity.isPending} cap={cap} arch={arch} />
      <HealthSection pending={validation.isPending} report={report} arch={arch} />
      <DecisionsSection pending={decisions.isPending} failed={decisions.isError} adrs={adrs} />
    </article>
  );
}
