import type { Metadata } from "next";

import { AppShell } from "@/components/layout/AppShell";
import { ProjectsView } from "@/features/projects/components/ProjectsView";

export const metadata: Metadata = { title: "Projects" };

export default function ProjectsPage() {
  return (
    <AppShell>
      <ProjectsView />
    </AppShell>
  );
}
