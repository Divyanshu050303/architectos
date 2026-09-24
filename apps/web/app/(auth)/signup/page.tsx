import type { Metadata } from "next";

import { AuthPanel } from "@/features/auth/components/AuthPanel";
import { safeNextPath } from "@/lib/safe-redirect";

export const metadata: Metadata = { title: "Create your account" };

export default async function SignupPage({ searchParams }: PageProps<"/signup">) {
  const { next, error } = await searchParams;
  return (
    <AuthPanel
      intent="signup"
      next={safeNextPath(next)}
      error={typeof error === "string" ? error : undefined}
    />
  );
}
