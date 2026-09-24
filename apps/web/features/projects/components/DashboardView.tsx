"use client";

import { Boxes, Plus } from "lucide-react";
import Link from "next/link";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { PageHeader } from "@/components/feedback/PageHeader";
import { Button } from "@/components/ui/button";
import { SkeletonGroup } from "@/components/ui/skeleton";
import { useProjects } from "@/hooks/use-projects";

import { openCreateProjectDialog } from "../create-project-store";
import { SystemCard, SystemCardSkeleton } from "./SystemCard";

/** "Your systems" (spec §46): systems, not generic analytics. */
export function DashboardView() {
  const { data: projects, isPending, isError, error, refetch } = useProjects();

  return (
    <div className="mx-auto flex w-full max-w-6xl flex-col gap-6 px-4 py-6 sm:px-6">
      <PageHeader
        title="Dashboard"
        description="The systems you are designing, with their latest analyzed health."
        actions={
          <>
            <Button asChild variant="ghost">
              <Link href="/projects">All projects</Link>
            </Button>
            <Button variant="primary" onClick={openCreateProjectDialog}>
              <Plus aria-hidden className="size-4" />
              New system
            </Button>
          </>
        }
      />

      <section aria-labelledby="systems-heading" className="flex flex-col gap-3">
        <h2 id="systems-heading" className="label-caps">
          Your systems
        </h2>
        {isPending ? (
          <SkeletonGroup label="Loading systems" className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            <SystemCardSkeleton />
            <SystemCardSkeleton />
            <SystemCardSkeleton />
          </SkeletonGroup>
        ) : isError ? (
          <DashboardError error={error} onRetry={() => void refetch()} />
        ) : projects.length === 0 ? (
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
        ) : (
          <ul className="grid gap-3 sm:grid-cols-2 xl:grid-cols-3">
            {projects.map((project) => (
              <li key={project.id} className="flex [&>a]:flex-1">
                <SystemCard project={project} />
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}

function DashboardError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const info = getErrorInfo(error);
  return (
    <ErrorState
      title="Your systems could not be loaded."
      message={info.message}
      requestId={info.requestId}
      onRetry={onRetry}
    />
  );
}
