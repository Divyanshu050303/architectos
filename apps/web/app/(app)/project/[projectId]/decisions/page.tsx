import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { DecisionsView } from "@/features/decisions/components/DecisionsView";

export const metadata: Metadata = { title: "Decisions" };

export default async function DecisionsPage(props: PageProps<"/project/[projectId]/decisions">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading decisions"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-80" />
        </SkeletonGroup>
      }
    >
      <DecisionsView projectId={projectId} />
    </Suspense>
  );
}
