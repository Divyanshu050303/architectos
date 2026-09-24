"use client";

import Link from "next/link";
import { useEffect } from "react";

import { ErrorState } from "@/components/feedback/ErrorState";
import { Button } from "@/components/ui/button";
import { logger } from "@/lib/logger";

/** Body of the route-level error.tsx boundaries. */
export function RouteError({
  error,
  retry,
  title = "This page could not be displayed.",
}: {
  error: Error & { digest?: string };
  retry: () => void;
  title?: string;
}) {
  useEffect(() => {
    logger.error("Route error boundary", { message: error.message, digest: error.digest ?? "" });
  }, [error]);
  return (
    <div className="mx-auto flex w-full max-w-2xl flex-col gap-3 px-4 py-10">
      <ErrorState
        title={title}
        message="An unexpected error occurred while rendering. Your saved work is not affected."
        requestId={error.digest}
        onRetry={retry}
        details={error.message}
      />
      <div>
        <Button asChild variant="ghost" size="sm">
          <Link href="/dashboard">Go to dashboard</Link>
        </Button>
      </div>
    </div>
  );
}
