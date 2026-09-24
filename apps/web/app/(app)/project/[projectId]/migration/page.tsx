import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { MigrationView } from "@/features/migration/components/MigrationView";

export const metadata: Metadata = { title: "Migration" };

export default async function MigrationPage(props: PageProps<"/project/[projectId]/migration">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading migration plans"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-80" />
        </SkeletonGroup>
      }
    >
      <MigrationView projectId={projectId} />
    </Suspense>
  );
}
