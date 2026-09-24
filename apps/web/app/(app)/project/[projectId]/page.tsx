import type { Metadata } from "next";

import { ProjectOverview } from "@/features/projects/components/ProjectOverview";

export const metadata: Metadata = { title: "Overview" };

export default async function ProjectOverviewPage({ params }: PageProps<"/project/[projectId]">) {
  const { projectId } = await params;
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
      <ProjectOverview projectId={projectId} />
    </div>
  );
}
