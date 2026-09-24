import Link from "next/link";

import { getErrorInfo } from "@/api/client";
import { EmptyState } from "@/components/feedback/EmptyState";
import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";

/** Shared failure state for project-scoped pages: a clear not-found, otherwise a retryable error. */
export function ProjectLoadError({ error, onRetry }: { error: unknown; onRetry: () => void }) {
  const info = getErrorInfo(error);
  if (info.code === "project_not_found") {
    return (
      <EmptyState
        title="This project does not exist."
        description="It may have been deleted, or the link is incorrect. Pick a system from the dashboard."
        action={
          <Button asChild variant="primary">
            <Link href="/dashboard">Go to dashboard</Link>
          </Button>
        }
      />
    );
  }
  return (
    <ErrorState
      title="The project could not be loaded."
      message={info.message}
      requestId={info.requestId}
      onRetry={onRetry}
    />
  );
}
