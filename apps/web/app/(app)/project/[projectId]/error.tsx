"use client";

import { RouteError } from "@/components/layout/RouteError";

/** Renders inside the project shell, so navigation stays usable. */
export default function ProjectError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return <RouteError error={error} retry={retry} title="This section could not be displayed." />;
}
