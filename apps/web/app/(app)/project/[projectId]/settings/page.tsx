import type { Metadata } from "next";

import { ProjectSettings } from "@/features/projects/components/ProjectSettings";

export const metadata: Metadata = { title: "Settings" };

export default async function SettingsPage({ params }: PageProps<"/project/[projectId]/settings">) {
  const { projectId } = await params;
  return (
    // Same page frame as every other project surface so headers align; the form column stays narrow.
    <div className="mx-auto w-full max-w-6xl px-4 py-6 sm:px-6">
      <div className="max-w-4xl">
        <ProjectSettings projectId={projectId} />
      </div>
    </div>
  );
}
