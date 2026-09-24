import { ProjectShell } from "@/components/layout/ProjectShell";

export default async function ProjectLayout({ params, children }: LayoutProps<"/project/[projectId]">) {
  const { projectId } = await params;
  return <ProjectShell projectId={projectId}>{children}</ProjectShell>;
}
