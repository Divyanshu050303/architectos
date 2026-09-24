import type { Metadata } from "next";

import { CostView } from "@/features/cost/components/CostView";

export const metadata: Metadata = { title: "Cost" };

export default async function CostPage(props: PageProps<"/project/[projectId]/cost">) {
  const { projectId } = await props.params;
  return <CostView projectId={projectId} />;
}
