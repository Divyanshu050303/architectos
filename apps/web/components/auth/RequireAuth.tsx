"use client";

import { Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { ErrorState } from "@/components/feedback/ErrorState";
import { useSession } from "@/hooks/use-auth";

/** Gate for every application route: without a session, go to /login and come back after. */
export function RequireAuth({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const { data: user, isPending, isError, refetch } = useSession();
  const signedOut = !isPending && !isError && user === null;

  useEffect(() => {
    if (!signedOut) return;
    const next = `${window.location.pathname}${window.location.search}`;
    router.replace(`/login?${new URLSearchParams({ next })}`);
  }, [signedOut, router]);

  if (isError) {
    return (
      <div className="flex min-h-dvh items-center justify-center p-4">
        <ErrorState
          title="Could not check your session."
          message="The ArchitectOS API did not respond. Nothing was lost; try again."
          onRetry={() => void refetch()}
          className="w-full max-w-md"
        />
      </div>
    );
  }

  if (isPending || signedOut) {
    return (
      <div role="status" className="flex min-h-dvh items-center justify-center gap-2 text-sm text-muted">
        <Loader2 aria-hidden className="size-4 animate-spin" />
        {signedOut ? "Redirecting to sign in…" : "Checking your session…"}
      </div>
    );
  }

  return children;
}
