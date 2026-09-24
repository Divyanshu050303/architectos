import { Workflow } from "lucide-react";
import Link from "next/link";

import { EmptyState } from "@/components/feedback/EmptyState";
import { Button } from "@/components/ui/button";

/** Spec §47: say what is missing and what to do next. */
export function CanvasEmptyState({ projectId }: { projectId: string }) {
  return (
    <div className="flex size-full items-center justify-center p-6">
      <EmptyState
        icon={Workflow}
        title="No architecture yet."
        description="Describe your system and ArchitectOS will generate a starting architecture."
        className="w-full max-w-lg bg-surface"
        action={
          <Button asChild variant="primary">
            <Link href={`/project/${projectId}/requirements`}>Describe system</Link>
          </Button>
        }
      />
    </div>
  );
}
