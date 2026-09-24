import type { Metadata } from "next";
import { Suspense } from "react";

import {
  ArchitectureWorkspace,
  WorkspaceSkeleton,
} from "@/features/architecture/components/ArchitectureWorkspace";

export const metadata: Metadata = { title: "Architecture" };

/** Thin route: the workspace reads URL state (useSearchParams), so it needs a Suspense boundary. */
export default function ArchitecturePage() {
  return (
    <Suspense fallback={<WorkspaceSkeleton />}>
      <ArchitectureWorkspace />
    </Suspense>
  );
}
