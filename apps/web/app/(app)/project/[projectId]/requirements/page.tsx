import type { Metadata } from "next";

import { RequirementsWorkspace } from "@/features/requirements/components/RequirementsWorkspace";

export const metadata: Metadata = { title: "Requirements" };

export default async function RequirementsPage({ params }: PageProps<"/project/[projectId]/requirements">) {
  const { projectId } = await params;
  return (
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
      <RequirementsWorkspace projectId={projectId} />
    </div>
  );
}
