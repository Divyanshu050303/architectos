import type { Metadata } from "next";

import { AuthPanel } from "@/features/auth/components/AuthPanel";
import { safeNextPath } from "@/lib/safe-redirect";

export const metadata: Metadata = { title: "Sign in" };

export default async function LoginPage({ searchParams }: PageProps<"/login">) {
  const { next, error, reset } = await searchParams;
  return (
    <AuthPanel
      intent="login"
      next={safeNextPath(next)}
      error={typeof error === "string" ? error : undefined}
      notice={reset === "done" ? "Your password was changed. Sign in with the new one." : undefined}
    />
  );
}
