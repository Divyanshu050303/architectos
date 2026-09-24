import type { Metadata } from "next";

import { ObservabilityView } from "@/features/observability/components/ObservabilityView";

export const metadata: Metadata = { title: "Observability" };

export default async function ObservabilityPage(props: PageProps<"/project/[projectId]/observability">) {
  const { projectId } = await props.params;
  return <ObservabilityView projectId={projectId} />;
}
