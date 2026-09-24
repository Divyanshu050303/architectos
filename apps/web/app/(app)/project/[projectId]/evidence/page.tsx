import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { EvidenceIndex } from "@/features/evidence/components/EvidenceIndex";

export const metadata: Metadata = { title: "Evidence" };

export default async function EvidencePage(props: PageProps<"/project/[projectId]/evidence">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading evidence"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-8" />
          <Skeleton className="h-64" />
        </SkeletonGroup>
      }
    >
      <EvidenceIndex projectId={projectId} />
    </Suspense>
  );
}
