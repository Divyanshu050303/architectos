import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { DiscoveryView } from "@/features/discovery/components/DiscoveryView";

export const metadata: Metadata = { title: "Infrastructure discovery" };

/** Thin route: the discovery view reads `?run=` (useSearchParams), so it needs a Suspense boundary. */
export default async function InfrastructurePage(props: PageProps<"/project/[projectId]/infrastructure">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading infrastructure discovery"
          className="mx-auto flex w-full max-w-7xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-72" />
          <Skeleton className="h-16" />
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-60" />
            ))}
          </div>
        </SkeletonGroup>
      }
    >
      <DiscoveryView projectId={projectId} />
    </Suspense>
  );
}
