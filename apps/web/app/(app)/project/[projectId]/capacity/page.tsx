import type { Metadata } from "next";

import { CapacityView } from "@/features/capacity/components/CapacityView";

export const metadata: Metadata = { title: "Capacity" };

export default async function CapacityPage(props: PageProps<"/project/[projectId]/capacity">) {
  const { projectId } = await props.params;
  return <CapacityView projectId={projectId} />;
}
