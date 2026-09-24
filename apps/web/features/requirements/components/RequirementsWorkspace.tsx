"use client";

import { useState } from "react";

import { getErrorInfo } from "@/api/client";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { ProvenanceTag } from "@/components/feedback/ProvenanceTag";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useRequirements } from "@/hooks/use-requirements";
import { formatRelativeTime } from "@/lib/formatting";

import { GenerationPanel } from "./GenerationPanel";
import { RequirementsForm } from "./RequirementsForm";

export function RequirementsWorkspace({ projectId }: { projectId: string }) {
  const { data: requirements, isPending, isError, error, refetch } = useRequirements(projectId);
  const [dirty, setDirty] = useState(false);

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Requirements"
        description="Describe the system and its scale. The architecture, capacity and validation all start here."
        meta={
          requirements ? (
            <>
              <ProvenanceTag kind="fact" />
              <span>
                {requirements.updatedAt
                  ? `Saved ${formatRelativeTime(requirements.updatedAt)}`
                  : "Not saved yet"}
              </span>
            </>
          ) : null
        }
      />
      {isPending ? (
        <SkeletonGroup label="Loading requirements" className="flex flex-col gap-4">
          <Skeleton className="h-32" />
          <Skeleton className="h-24" />
          <Skeleton className="h-20" />
        </SkeletonGroup>
      ) : isError ? (
        <ErrorState
          title="Requirements could not be loaded."
          message={getErrorInfo(error).message}
          requestId={getErrorInfo(error).requestId}
          onRetry={() => void refetch()}
        />
      ) : (
        <div className="grid items-start gap-6 xl:grid-cols-[minmax(0,1fr)_22rem]">
          <RequirementsForm
            key={requirements.updatedAt ?? "unsaved"}
            projectId={projectId}
            requirements={requirements}
            onDirtyChange={setDirty}
          />
          <div className="xl:sticky xl:top-6">
            <GenerationPanel
              projectId={projectId}
              requirementsSaved={Boolean(requirements.updatedAt && requirements.description.trim())}
              requirementsDirty={dirty}
            />
          </div>
        </div>
      )}
    </div>
  );
}
