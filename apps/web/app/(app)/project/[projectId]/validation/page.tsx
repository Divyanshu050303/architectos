import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { ValidationView } from "@/features/validation/components/ValidationView";

export const metadata: Metadata = { title: "Validation" };

export default async function ValidationPage(props: PageProps<"/project/[projectId]/validation">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading validation results"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-16" />
          <Skeleton className="h-40" />
        </SkeletonGroup>
      }
    >
      <ValidationView projectId={projectId} />
    </Suspense>
  );
}
