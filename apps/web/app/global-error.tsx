"use client";

import "@/styles/globals.css";

import { OctagonAlert, RotateCw } from "lucide-react";
import Link from "next/link";

import { Button } from "@/components/ui/button";
import { themeInitScript } from "@/providers/theme-provider";

/**
 * Replaces the root layout when it fails, so it cannot rely on providers or shared
 * components that need them. Applies the saved theme itself.
 */
export default function GlobalError({
  error,
  retry,
}: {
  error: Error & { digest?: string };
  retry: () => void;
}) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <title>Error · ArchitectOS</title>
        <script dangerouslySetInnerHTML={{ __html: themeInitScript }} />
      </head>
      <body>
        <main className="mx-auto flex min-h-dvh w-full max-w-lg flex-col justify-center px-4">
          <div
            role="alert"
            className="flex gap-3 rounded-md border border-danger/40 bg-danger-soft px-4 py-3"
          >
            <OctagonAlert aria-hidden className="mt-0.5 size-4 shrink-0 text-danger-fg" />
            <div className="flex flex-col gap-2 text-sm">
              <p className="font-semibold text-danger-fg">ArchitectOS could not start.</p>
              <p className="text-fg-secondary">
                An unexpected error occurred while loading the application. Your saved work is not affected.
              </p>
              {error.digest ? (
                <p className="text-xs text-muted">
                  Error ID: <span className="tabular text-fg-secondary">{error.digest}</span>
                </p>
              ) : null}
              <div className="flex gap-2">
                <Button size="sm" onClick={retry}>
                  <RotateCw aria-hidden className="size-3.5" />
                  Retry
                </Button>
                <Button asChild size="sm" variant="ghost">
                  <Link href="/dashboard">Go to dashboard</Link>
                </Button>
              </div>
            </div>
          </div>
        </main>
      </body>
    </html>
  );
}
