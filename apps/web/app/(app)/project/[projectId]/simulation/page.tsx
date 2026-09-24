import type { Metadata } from "next";
import { Suspense } from "react";

import { Skeleton, SkeletonGroup } from "@/components/ui/skeleton";
import { SimulationView } from "@/features/simulation/components/SimulationView";

export const metadata: Metadata = { title: "Simulation" };

export default async function SimulationPage(props: PageProps<"/project/[projectId]/simulation">) {
  const { projectId } = await props.params;
  return (
    <Suspense
      fallback={
        <SkeletonGroup
          label="Loading simulation"
          className="mx-auto flex w-full max-w-6xl flex-col gap-6 p-4 sm:p-6"
        >
          <Skeleton className="h-10 w-64" />
          <Skeleton className="h-80" />
        </SkeletonGroup>
      }
    >
      <SimulationView projectId={projectId} />
    </Suspense>
  );
}
