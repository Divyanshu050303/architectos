import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { DriftView } from "@/features/drift/components/DriftView";

export const metadata: Metadata = { title: "Drift" };

/** Thin route: the drift view reads `?show=` (useSearchParams), so it needs a Suspense boundary. */
export default async function DriftPage(props: PageProps<"/project/[projectId]/drift">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading drift"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-20" />
          <Skeleton className="h-72" />
        </SkeletonGroup>
      }
    >
      <DriftView projectId={projectId} />
    </Suspense>
  );
}
