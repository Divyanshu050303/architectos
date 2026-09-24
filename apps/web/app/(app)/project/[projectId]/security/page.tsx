import type { Metadata } from "next";

import { SecurityView } from "@/features/security/components/SecurityView";

export const metadata: Metadata = { title: "Security" };

export default async function SecurityPage(props: PageProps<"/project/[projectId]/security">) {
  const { projectId } = await props.params;
  return <SecurityView projectId={projectId} />;
}
