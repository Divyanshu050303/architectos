import type { Metadata } from "next";

import { ReliabilityView } from "@/features/reliability/components/ReliabilityView";

export const metadata: Metadata = { title: "Reliability" };

export default async function ReliabilityPage(props: PageProps<"/project/[projectId]/reliability">) {
  const { projectId } = await props.params;
  return <ReliabilityView projectId={projectId} />;
}
