"use client";

import { Boxes, Plus } from "lucide-react";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Button } from "@/components/ui/button";
import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { useProjects } from "@/hooks/use-projects";

import { openCreateProjectDialog } from "../create-project-store";
import { ProjectTable } from "./ProjectTable";

export function ProjectsView() {
  const { data: projects, isPending, isError, error, refetch } = useProjects();
  const info = isError ? getErrorInfo(error) : null;

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6">
      <PageHeader
        title="Projects"
        description="Every system in this workspace."
        actions={
          <Button variant="primary" onClick={openCreateProjectDialog}>
            <Plus aria-hidden className="size-4" />
            New system
          </Button>
        }
      />
      {isPending ? (
        <SkeletonGroup label="Loading projects" className="flex flex-col gap-2">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-10" />
          ))}
        </SkeletonGroup>
      ) : info ? (
        <ErrorState
          title="Projects could not be loaded."
          message={info.message}
          requestId={info.requestId}
          onRetry={() => void refetch()}
        />
      ) : projects && projects.length > 0 ? (
        <div className="rounded-md border border-default bg-surface">
          <ProjectTable projects={projects} />
        </div>
      ) : (
        <EmptyState
          icon={Boxes}
          title="No systems yet."
          description="Describe your system and ArchitectOS will generate a starting architecture."
          action={
            <Button variant="primary" onClick={openCreateProjectDialog}>
              Create system
            </Button>
          }
        />
      )}
    </div>
  );
}
