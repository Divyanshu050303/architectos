import type { Metadata } from "next";

import { ArchitectureReport } from "@/features/reports/components/ArchitectureReport";

export const metadata: Metadata = { title: "Architecture report" };

export default async function ReportsPage(props: PageProps<"/project/[projectId]/reports">) {
  const { projectId } = await props.params;
  return <ArchitectureReport projectId={projectId} />;
}
