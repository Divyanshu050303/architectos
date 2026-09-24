"use client";

import { useQueryClient } from "@tanstack/react-query";
import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";

import { getSession } from "@/api/auth";
import { ErrorState } from "@/components/feedback/ErrorState";
import { queryKeys } from "@/lib/query-keys";

/**
 * Landing page of the OAuth round trip. The API has already set the session cookie (or put
 * ?error= on the URL); this confirms the session and moves on to `next`.
 */
export function AuthCallback({ next, error }: { next: string; error?: string }) {
  const router = useRouter();
  const queryClient = useQueryClient();
  const [attempt, setAttempt] = useState(0);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    if (error) {
      router.replace(`/login?${new URLSearchParams({ error, next })}`);
      return;
    }
    let cancelled = false;
    queryClient
      .fetchQuery({
        queryKey: queryKeys.session(),
        queryFn: ({ signal }) => getSession(signal),
        staleTime: 0,
      })
      .then((user) => {
        if (cancelled) return;
        router.replace(user ? next : `/login?${new URLSearchParams({ error: "session_missing", next })}`);
      })
      .catch(() => {
        if (!cancelled) setFailed(true);
      });
    return () => {
      cancelled = true;
    };
  }, [error, next, router, queryClient, attempt]);

  if (failed) {
    return (
      <ErrorState
        title="Could not finish signing in."
        message="The ArchitectOS API did not confirm your session."
        onRetry={() => {
          setFailed(false);
          setAttempt((n) => n + 1);
        }}
        className="w-full max-w-sm"
      />
    );
  }

  return (
    <p role="status" className="flex items-center gap-2 text-sm text-fg-secondary">
      <Loader2 aria-hidden className="size-4 animate-spin" />
      Signing you in…
    </p>
  );
}
