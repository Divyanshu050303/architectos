import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { EvolutionView } from "@/features/evolution/components/EvolutionView";

export const metadata: Metadata = { title: "Evolution" };

export default async function EvolutionPage(props: PageProps<"/project/[projectId]/evolution">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading evolution roadmap"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-80" />
        </SkeletonGroup>
      }
    >
      <EvolutionView projectId={projectId} />
    </Suspense>
  );
}
